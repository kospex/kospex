# Using --platform is to force it to be compatible on mac M chips.
# The mergestat .so file requires this for the moment. 
docker build -t kospex:`date +%s` -t kospex:latest --platform linux/amd64 .
