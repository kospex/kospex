#!/bin/sh

# This script is used to test the CLI interface of kospex
set -e

# check that kospex runs without arguments
./kospex

# check that kospex summary runs without arguments
./kospex summary

