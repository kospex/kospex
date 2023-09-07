#!/bin/sh

set -e
echo "\nAbout to start the kospex docker shell environment ...\n"
KCMD=`basename $0`

USAGE1="Usage: $KCMD [GIT_DATA_DIRECTORY] [KOSPEX_DATA_DIR]"
CREATE_KOSPEX_DIR="\nEither create one using\n mkdir \$HOME/kospex\n or use the second parameter to specify a directory\n"

# Don't display the following for MVP, this will just be used for development and 
# a third parameter is passed in for the development path
USAGE2="Usage: $0 [GIT_DATA_DIRECTORY] [KOSPEX_DATA_DIR] [KOSPEX_DEV_DIR]\n\
\t where [KOSPEX_DEV_DIR] is the path to the kospex source code repo to mount for development"

if [ "$#" == 1 ]; then
    # Use ~/kospex as the default data directory
    GIT_DATA_DIR=$1
    if [ -d "$HOME/kospex" ]; then 
        echo "Found a kospex directory in your \$HOME directory, using that as the KOSPEX_DATA_DIR"
        KOSPEX_DATA_DIR="$HOME/kospex"
    else
        echo "\nNo 'kospex' directory found in your home directory"
        echo $CREATE_KOSPEX_DIR
        echo $USAGE1
        echo ""
        exit 1
    fi

elif [ "$#" == 2 ]; then
    GIT_DATA_DIR=$1
    KOSPEX_DATA_DIR=$2

elif [ "$#" == 3 ]; then
    GIT_DATA_DIR=$1
    KOSPEX_DATA_DIR=$2
    KOSPEX_DEV_PATH=$3

else
    echo "Incorrect number of parameters!\n"
    echo $USAGE1
    echo ""
    exit 1
fi

# Docker volumes need full path, needed to mounting in docker, comes from the following stackoverflow answer
# https://stackoverflow.com/questions/61232962/getting-the-calling-script-path-from-a-bash-script

# Check the directories exist

if [ -d "$GIT_DATA_DIR" ]; then
    # Set the absolute path
    GIT_DATA_DIR=$(cd $GIT_DATA_DIR && pwd)
    echo $GIT_DATA_DIR
else
    echo "GIT_DATA_DIR '$GIT_DATA_DIR' does not exist"
    exit 1
fi

if [ -d "$KOSPEX_DATA_DIR" ]; then
    # Set the absolute path
    KOSPEX_DATA_DIR=$(cd $KOSPEX_DATA_DIR && pwd)
    echo $KOSPEX_DATA_DIR
else
    echo "KOSPEX_DATA_DIR '$KOSPEX_DATA_DIR' does not exist"
    echo $CREATE_KOSPEX_DIR
    exit 1
fi

# DEVELOPER MODE
if [ "$#" == 3  ]; then

    if [ -d "$KOSPEX_DEV_PATH" ]; then
        # Set the absolute path
        KOSPEX_DEV_PATH=$(cd $KOSPEX_DEV_PATH && pwd)
    else
        echo "KOSPEX_DEV_PATH '$KOSPEX_DEV_PATH' does not exist"
        exit 1
    fi

    echo "About to run in [DEV]eloper mode"
    echo docker run -it -v "$GIT_DATA_DIR":/data -v "$KOSPEX_DATA_DIR:/root/kospex" -v "$KOSPEX_DEV_PATH:/app" --rm kospex:latest
    docker run -it -v "$GIT_DATA_DIR":/data -v "$KOSPEX_DATA_DIR:/root/kospex" -v "$KOSPEX_DEV_PATH:/app" --rm kospex:latest

# NORMAL MODE
else

    # Run in normal mode
    echo "About to run"
    echo 'docker run -it -v "$GIT_DATA_DIR":/data -v "$KOSPEX_DATA_DIR:/root/kospex" --rm kospex:latest'
    docker run -it -v "$GIT_DATA_DIR":/data -v "$KOSPEX_DATA_DIR:/root/kospex" --rm kospex:latest

fi

