#!/bin/sh

#runs inside vm

home=$1
cd $home

get_vm_ip()
{
	echo "Adresa IP a masinii virtuale este:"
	ifconfig |grep "inet addr"|cut -d ':' -f 2|cut -d " " -f 1|head -n 1 2>&1
}


install_job()
{
	echo "unpacking ..."
	unzip -o file.zip 2>&1
	return $?
}

build_job()
{
	echo -e "\nchecker: building" 
	echo "fixing file dates ..."
	/usr/bin/find . | xargs touch 2>&1
	make build 2>&1
	if [ "$?" != 0 ]; then
		echo "checker: building failed"
		return 1       
	fi
	
	echo "checker: building done"
	return 0
}

install_tests()
{
	unzip -o tests.zip 1>&2
	return $?	
}

build_tests()
{
	if [ -f Makefile.checker ]; then make -f Makefile.checker build-$1; else echo dont know how to build tests; fi  2>&1 
	
	return  0
}

check_job()
{
	get_vm_ip;

	echo -e "\nchecker: checking "

	install_job; err=$?
	if [ $err != 0 ]; then
		return $err
	fi

	install_tests; err=$?
	if [ $err != 0 ]; then
		return $err
	fi

	build_tests "pre" ; err=$?
	if [ $err != 0 ]; then
		return $err
	fi

	build_job; err=$?
	if [ $err != 0 ]; then
		return $err
	fi

	build_tests "post" ; err=$?
	if [ $err != 0 ]; then
		return $err
	fi


	return 0
}

main()
{
    
	# test it
	check_job  > job_build 2>job_errors;  err=$?
     
	return $err
}
 
main 
