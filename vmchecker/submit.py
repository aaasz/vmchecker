#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""Creates a homework configuration file, uploads files to repository
and sends the homework for evaluation

For a better submission scheme see the commit:
    22326889e780121f37598532efc1139e21daab41

"""

from __future__ import with_statement

import ConfigParser
import os
import shutil
import subprocess
import tempfile
import time
import datetime
from contextlib import closing

from . import config
from . import paths
from . import ziputil
from . import submissions
from . import vmlogging
from .courselist import CourseList

logger = vmlogging.create_module_logger('submit')

class SubmitedTooSoonError(Exception):
    """Raised when a user sends a submission too soon after a previous one.

    This is used to prevent a user from DOS-ing vmchecker or from
    monopolising the test queue."""
    def __init__(self, message):
        Exception.__init__(self, message)

def submission_config(user, assignment, course_id, upload_time,
                      storer_result_dir, storer_username, storer_hostname):
    """Creates a configuration file describing the current submission:
       - who uploaded it
       - which assignment does it solve
       - which course was it for
       - when was it uploaded

       Also, XXX, should be removed:
       - where to store results
       - with which user to connect to the machine storring the results
       - which is the machine storring the results

       The last part should not be part of the submission config, but
       should be generated automatically when the submission is sent
       for testing.
    """
    sbcfg = ConfigParser.RawConfigParser()
    sbcfg.add_section('Assignment')
    sbcfg.set('Assignment', 'User', user)
    sbcfg.set('Assignment', 'Assignment', assignment)
    sbcfg.set('Assignment', 'UploadTime', upload_time)
    sbcfg.set('Assignment', 'CourseID', course_id)

    # XXX these should go to `callback'
    sbcfg.set('Assignment', 'ResultsDest', storer_result_dir)
    sbcfg.set('Assignment', 'RemoteUsername', storer_username)
    sbcfg.set('Assignment', 'RemoteHostname', storer_hostname)
    return sbcfg



def submission_backup_prefix(course_id, assignment, user, upload_time):
    """Backups have a name of the form:
    SO_1-minishell-linux_Lucian Adrian Grijincu_2010.03.05 01:08:54_juSZr9

    This builds the prefix of the path. The random last part is
    generated by Python's tempfile module.
    """
    return '%s_%s_%s_%s_' % (course_id, assignment, user, upload_time)


def submission_backup(back_dir, archive_filename, sbcfg):
    """Make a backup for this submission.

    Each entry is of the following structure:
    +--$back_dir/
    |  +--archive/
    |  |  +-- X         (all the files from the archive)
    |  |  +-- Y         (all the files from the archive)
    |  +--config        config describing the submission
    |  |                (user, uploadtime, assignment)
    |  +--archive.zip   the original (unmodified) archive
    """
    back_arc = paths.dir_submission_expanded_archive(back_dir)
    back_cfg = paths.submission_config_file(back_dir)
    back_zip = paths.submission_archive_file(back_dir)

    # make sure the directory path exists
    if not os.path.exists(back_dir):
        os.makedirs(back_dir)

    # copy the (unmodified) archive. This should be the first thing we
    # do, to make sure the uploaded submission is on the server no
    # matter what happens next
    shutil.copy(archive_filename, back_zip)

    # write the config. Again do this before unzipping (which might fail)
    # to make sure we have the upload data ready.
    with open(back_cfg, 'w') as handle:
        sbcfg.write(handle)

    # unzip the archive, but check if it has absolute paths or '..'
    ziputil.unzip_safely(archive_filename, back_arc)

    logger.info('Stored submission in temporary directory %s', back_dir)



def submission_git_commit(dest, user, assignment):
    """Submit in git the data from the dest subdirectory of the
    repository.
    """
    subprocess.Popen(['git', 'add', '--force', '.'], cwd=dest).wait()
    subprocess.Popen(['git', 'commit', '--allow-empty', '.',
                      '-m "Updated ' + user + '\' submission for ' +
                      assignment + '"'], cwd=dest).wait()




def save_submission_in_storer(archive_filename, user, assignment,
                              course_id, upload_time):
    """ Save the submission on the storer machine:

        - create a config for the submission to hold identifying info
          (user, course, assignment, upload_time)
        - create a backup for the submission parallel to the git repo
        - commit the backup in the git repo
        - copy the archive near the data committed in the repo to be
          easily accessible.
    """
    vmcfg = config.VmcheckerConfig(CourseList().course_config(course_id))
    vmpaths = paths.VmcheckerPaths(vmcfg.root_path())
    sbroot = vmpaths.dir_submission_root(assignment, user)
    sbcfg = submission_config(user, assignment, course_id, upload_time,
                              paths.dir_submission_results(sbroot),
                              vmcfg.storer_username(),
                              vmcfg.storer_hostname())

    # make a separate backup for each submission, parallel to the git repo
    # here, each submission gets a separate entry.
    # in git the former entryes get overwritten and commited to git.
    name_prefix = submission_backup_prefix(course_id, assignment, user, upload_time)
    back_dir = tempfile.mkdtemp(prefix=name_prefix, dir=vmpaths.dir_backup())
    submission_backup(back_dir, archive_filename, sbcfg)


    # commit in git this submission
    git_dest = vmpaths.dir_submission_root(assignment, user)
    with vmcfg.assignments().lock(vmpaths, assignment):
        # cleanup any previously commited data
        if os.path.exists(git_dest):
            shutil.rmtree(git_dest)
        submission_backup(git_dest, archive_filename, sbcfg)
        # we only commit the archive's data. the config file and the
        # archive.zip is not commited.
        submission_git_commit(paths.dir_submission_expanded_archive(git_dest),
                              user, assignment)



def create_testing_bundle(user, assignment, course_id):
    """Creates a testing bundle.

    This function creates a zip archive (the bundle) with everything
    needed to run the tests on a submission.

    The bundle contains:
        config      - assignment config (eg. name, time of submission etc)
        archive.zip - a zip containing the sources
        tests.zip   - a zip containing the tests
        ???         - assignment's extra files (see Assignments.include())

    """
    vmcfg = config.VmcheckerConfig(CourseList().course_config(course_id))
    vmpaths = paths.VmcheckerPaths(vmcfg.root_path())
    sbroot = vmpaths.dir_submission_root(assignment, user)

    asscfg  = vmcfg.assignments()
    machine = asscfg.get(assignment, 'Machine')

    #rel_file_list = list(vmcfg.assignments().files_to_include(assignment))
    rel_file_list = []
    rel_file_list += [ ('build.sh', vmcfg.get(machine, 'BuildScript')),
                       ('run.sh',   vmcfg.get(machine, 'RunScript')) ]
    rel_file_list += [ ('archive.zip', paths.submission_archive_file(sbroot)),
                       ('tests.zip', vmcfg.assignments().tests_path(vmpaths, assignment)),
                       ('config', paths.submission_config_file(sbroot)) ]

    file_list = [ (dst, vmpaths.abspath(src)) for (dst, src) in rel_file_list ]

    # builds archive with configuration
    with vmcfg.assignments().lock(vmpaths, assignment):
        # creates the zip archive with an unique name
        (bundle_fd, bundle_path) = tempfile.mkstemp(
            suffix='.zip',
            prefix='%s_%s_%s_' % (course_id, assignment, user),
            dir=vmpaths.dir_unchecked())  # FIXME not here
        logger.info('Creating bundle package %s', bundle_path)

        try:
            with closing(os.fdopen(bundle_fd, 'w+b')) as handler:
                ziputil.create_zip(handler, file_list)
        except:
            logger.error('Failed to create zip archive %s', bundle_path)
            os.unlink(bundle_path)
            raise # just cleaned up the bundle. the error still needs
                  # to be reported.

    return bundle_path


def ssh_bundle(bundle_path, vmcfg):
    """Sends a bundle over ssh to the tester machine"""
    tester_username  = vmcfg.tester_username()
    tester_hostname  = vmcfg.tester_hostname()
    tester_queuepath = vmcfg.tester_queue_path()
    cmd = [ 'scp', bundle_path,
            '%s@%s:%s' % (tester_username, tester_hostname, tester_queuepath)]
    logger.info('Invoking submission script %s', cmd)
    try:
        subprocess.check_call(cmd)
    except:
        logger.fatal('Cannot evaluate submission %s', bundle_path)
        os.unlink(bundle_path)
        raise



def submitted_too_soon(assignment, user, vmcfg):
    """Check if the user submitted this assignment very soon after
    another submission.

    Returns True if another submission of this assignment was done
    without waiting the amount of time specified in the config file.
    """
    vmpaths = paths.VmcheckerPaths(vmcfg.root_path())
    subm = submissions.Submissions(vmpaths)
    if not subm.submission_exists(assignment, user):
        return False
    upload_time = subm.get_upload_time(assignment, user)

    if upload_time is None:
        return False

    remaining = upload_time
    remaining += vmcfg.assignments().timedelta(assignment)
    remaining -= datetime.datetime.now()

    return remaining > datetime.timedelta()



def queue_for_testing(assignment, user, course_id):
    """Queue for testing the last submittion for the given assignment,
    course and user."""
    vmcfg = config.VmcheckerConfig(CourseList().course_config(course_id))
    bundle_path = create_testing_bundle(user, assignment, course_id)
    ssh_bundle(bundle_path, vmcfg)


def submit(archive_filename, assignment, user, course_id,
           skip_time_check=False, forced_upload_time=None):
    """Commit in the git repo and queue for testing a new submission.

    The submission is identified by archive_filename.

    Implicitly, if the user sent the submission to soon, it isn't
    queued for checking. This check can be skipped by setting
    skip_time_check=True.

    If forced_upload_time is not specified, the current system time is
    used.
    """
    vmcfg = config.VmcheckerConfig(CourseList().course_config(course_id))

    if forced_upload_time != None:
        skip_time_check = True
        upload_time = forced_upload_time
    else:
        upload_time = time.strftime(config.DATE_FORMAT)

    # checks time difference
    if not skip_time_check and submitted_too_soon(assignment, user, vmcfg):
        raise SubmitedTooSoonError('''You are submitting too fast.
                                   Please allow %s between submissions''' %
                                   str(vmcfg.assignments().timedelta(assignment)))

    save_submission_in_storer(archive_filename, user, assignment,
                              course_id, upload_time)
    queue_for_testing(assignment, user, course_id)

