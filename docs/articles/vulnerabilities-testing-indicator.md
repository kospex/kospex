# Are security vulnerabilities an indicator of development testing practices?

*TLDR*: Security vulnerabilities might be an indicator of low automated unit, integration and regression testing capabilities. Finding vulnerabilities is easier than fixing them as bug fixes need to be tested.  Identifying libraries that are getting behind in versions is one indicator of code repository health.

When I first meet a development team, I like to ask three questions to gain an understanding of where they are at:
- How often do you release? (e.g. daily, weekly, monthly, quarterly, on demand or adhoc)
- How often can you release? (e.g. they can release quickly but don't)
- How much manual testing is required per release?

While answers can be subjective, when there is a higher manual testing and a longer release cycle it more likely it's going to be harder (or more intensive) to test security fixes and their impacts.

Keep in mind, each application is different, so when you have hundreds of apps, there's going to be a LOT of diversity in release frequency and test automation across a large organisation.  You can't ask this at an organisation with any accuracy, it needs to be per application.

Let's use automated opensource library testing with SCA (Software Composition Analysis) as "the indicator".

## Setting the scene

Often, when I work on a current state discovery of the source code estate, I'll start with access to the source code repositories and use whatever SAST (Static Application Security Testing) and SCA (Software Composition Analysis) tools are on hand. If they don't have anything, i'll use an opensource tool as a starting point. Typically there's good enough tools to look at opensource libraries and find known vulnerabilities.
After looking at the code repositories, we can get a bit of a heat map and then ask some questions around how software bugs and security vulnerabilities are handled.
I've been in similar conversations over the years, that follow this pattern.

*Security*: We have several high or critical vulnerabilities in a few libraries, they are several months or years old. How hard is it to fix them by bumping these versions and re-testing? \
*Dev*: It's most manual testing, but we also need to upgrade the language version first for one of these to be able to bump the library version.

*Security*: So, how hard is that to upgrade test the language version upgrade? \
*Dev*: Well, we're also running an older database, so the library for that needs to be tested too. Not sure if we can use the older database library with the newer language, or if the newer version of the library works with the older database version. Also, we'll need to test if there are breaking changes in the major version change.

So herein lies the problem, the security person can take a look at code and find vulnerabilities. Without knowing the code and being able to write fixes, all we've got is visibility, and in this case, there's an underlying lack of automated unit, integration and regression testing.

So while the "fix" might be to bump a library or component version from 2.0 to 3.0, the actual challenge is being able to test if it works or breaks.
Now some people might say, 2.0 to 3.0 is a major change. We'll that's true, but even 2.1 to 2.2 might have breaking changes. Often teams get stuck when they haven't updated a library version over months and even years and now it's end of life.
By asking about the fix process we can understand the "why" a vulnerability might be harder and take longed to fix.

## The number of versions behind can be a useful indicator
The more versions behind you are, I would argue, the more likely you are to have vulnerabilities and also trouble updating a library. I've used a site called deps.dev to agnostically check libraries for both vulnerabilities and versions behind. There's also quite a few developer tools which look at versions behind as a way of improving maintenance.

[Kospex](/kospex) has a feature to find the versions behind in a language package manager file.
> kospex deps -file requirements.txt

The output will show the last update date, versions behind, known vulnerabilities and the Git repo location if known.

There is another indicator of "last change". Again, the thinking is the older the library the more likely for vulnerabilties or upgrade challenges. Not always accurate through, one Python date library is years old, but is still the current version, and from SCA tools i've used, both opensource and commercial, it's not vulnerable.
The age of updates is almost a complete topic to itself. If something hasn't been updated in a year, is it feature complete and stable? or has it been abandoned?

## What's the "magic number" for reasonable versions behind?
Apparently, it's on average 2.7 versions behind, according to Sonataype in their [State of the Software Supply Chain](https://www.sonatype.com/resources/whitepapers/2021-state-of-the-software-supply-chain-report-2021), 2021.
Basically, latest is not always best, as it might have breaking changes and too much older and their might be bugs or vulnerabilities you don't want.
From conversations over the last 6 months, it seems like development managers and operations have said that keeping versions of anything N - 2 is a good target.

## What's next?
I think everyone should run an analysis process over their git repositories to look for:
- Old and orphaned repositories (and do some cleanup)
- Check the age of dependency files
- Check how far behind the versions of their opensource libraries are
- Use SCA tooling to assist with automation of vulnerability detection

Running a code analysis process is useful for understanding current state and possibly hotspots to focus on. Ideally, use this data as a "predictive maintenance" indicator.

Get ready for some people and process stuff, and possibly budget conversations. Having to do remediation by upgrading a library versions can take days, weeks or even months of effort.

As mentioned earlier, finding is easy, fixing is harder, takes time and requires testing.
