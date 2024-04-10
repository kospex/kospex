# What is a Git Twin?

_A Git twin is a file system which continuously syncs repos from the original source provider/s._

That's our definition. When we have a twin, we can analyse the code and history of changes "locally".
A twin allows for different methods of filesystem analysis, but still being able to see developers work (commits and changes) and the time series nature of changes. 

This idea has been adapted from, a "ditial twin".

_A digital twin is a virtual representation of an object or system designed to reflect a physical object accurately. It spans the object's lifecycle, is updated from real-time data and uses simulation, machine learning and reasoning to help make decisions._

Source: [IBM, Digital Twin](https://www.ibm.com/topics/what-is-a-digital-twin)

Here's another definition from AWS.

_A digital twin is a virtual model of a physical object. It spans the object's lifecycle and uses real-time data sent from sensors on the object to simulate the behavior and monitor operations. Digital twins can replicate many real-world items, from single pieces of equipment in a factory to full installations, such as wind turbines and even entire cities. Digital twin technology allows you to oversee the performance of an asset, identify potential faults, and make better-informed decisions about maintenance and lifecycle._

[AWS, Digital Twin](https://aws.amazon.com/what-is/digital-twin/)

A Git twin is a virtual clone of a virtual object (source code and history). We'll use the twin for analysis without impacting the source system or repository.  

## Why do local analysis "on disk" instead of using API calls?

Many tools operate on files or the filesytem and are "git ignorant". Often, we want to be able to use these tools for analysis. 

When dealing with thousands of files and large repositories (several hundreds megabytes), network latency can be an issue from both time (lag) and money (transfer costs). 

## Filesystem layout for a Git Twin

Here's the kospex model for the Git twin layout

/KOSPEX_CODE/GIT_SERVER/ORG_OWNER/REPO

| Example repo                           | Git twin filepath                    |
| ------------                           | -----------------                    |
| https://github.com/mergestat/mergestat | /code/github.com/mergestat/mergestat |
| https://github.com/kospex/kospex/      | /code/github.com/kospex/kospex       |
| https://go.googlesource.com/text       | /code/go.googlesource.com/text       | 


## How is this different from a git mirror?

Effectively this approach aims to mirror a lot of repositories. 
Mirroring is more about redundancy and access, where as the twin is more about analytics.

We're borrowing the "digital twin" concept, where we are observing the asset to identify to identify faults and areas for maintenance.
