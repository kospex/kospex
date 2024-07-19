# krunner

The krunner command is used to run a command on all the git repos in a directory. 
Most of the commands wont use the kospex database with the notable exception of "git-pull".

## git-pull

The format for this command is

> krunner git-pull DIRECTORY

By default, this will find all repos in DIRECTORY and attempt a "git pull" as your user. 

**NOTE** - This will NOT download new repositories, it will only sync existing ones thare are aleady on disk.


## find-docker

This command finds files with the naming convention of Dockerfile and docker-*.yml

> krunner find-docker DIRECTORY

Useful additional switches

 \- images

will attemp to extract the image names from any of the files name and provide a summary of container regisgty as well. 

So, if you want to find all the docker files and images, run the following command:

> krunner find-docker -images DIRECTORY






