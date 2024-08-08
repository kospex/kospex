# What are orphaned repos and why should you care

*TLDR:* If no one knows the code, how will you fix or redeploy it? Knowledge of code is important to maintain, operate and make bug or security fixes. However, removing unused repos might improve focus by removing clutter. 

In the book, The Mythical Man-Month by Fred Brooks, it talks about disproportionate amount of time developers spend thinking compared to coding. Some say that that source code is only 30% of the development process, with thinking, discussions and discovery being the other 70%. So even if we've got the code, we're missing the thinking which is still in the developers head. Even documentation might not do justice to that days or weeks of thinking and discussions which occurred.  

Some of the "why we did this?" and "that failed so we did it this way" will be missing when the next person comes along. 

I am pretty sure if you ask most developers, reading someone else's code, and changing it, with little context is hard. 

Seems like companies are experiencing between 15-25% churn in their developers, either through voluntary "change of scenery" or "resizing", which can cause working knowledge gaps as well as orphaned repos.  

*What is an orphaned repo?*
An orphaned repo (Git code repository) is where no developers who've contributed code work for your organisation. 
Maybe, is not an issue if the repo is old and unused, so we can delete or achive. 
However, there can still be challenges if: 
- The code is in use - now we have an operational risk if something goes wrong
- It's old, maybe unused, but has secrets or passwords. Do we archive? do we clean the secrets before archiving?
- Is there intellectual property we care about but might not be using?

*Why is this important? My other developers know language X used in this repo?*

Context is hard to transfer. Just like picking up a recipe you've never cooked before, it's going to take more time even if you can read the instructions.  

The knowledge domain could be different. Just because we can read and write in a language, does not mean we can write a legal contract in that language (unless we have that expertise or experience). 

A company I know who takes on maintenance of applications for other companies said that it often takes a developer around six weeks to be comfortable with the code to be able to maintain and improve it from a cold start. 

When something is broken and you need to patch a security vulnerability or deploy again, it's a lot easier if you've done it before. 

*How to identify orphaned repos.*

There a two methods of identifying orphaned repos, with varied degrees of accuracy. Both methods requiring looking at the commits or changes to a repository, but the source of "who works here" is different. The method is simple, find all the developers (typically email addresses) in a code repository, see how many appear in our "active" developer or employee list. 

_#1  Use a staff directory or HR Employee extract and cross reference developer commit history in the repo (Most accurate)_

Compare people in a repo to an  extract from HR or staff directory, will give an accurate can be more accurate, compared to the lack of activity method used above. 
The crux of this method requires the extract or ability to query a staff directory, then looking at the developers in each repository and checking if they are in the extract.
 
_#2 Use version control history and see if developers in a repo are still committing in other repositories_

Some places use definitions of an active developer having activity in say 30 or 90 days. Using this method we can stay within git to check if users in a repo have been seen in other repos and consider them "gone" if they haven't been active in X days.

The key challenge with this method is that we're "guessing" someone has left if they haven't committed and it takes a while to consider them gone. So the feedback loop is much better if you can query a HR extract or lookup a directory.

*Can we do Predictive Maintenance? (AKA can we avoid a single point of failure?)*

Sure can. Basically, if only one person (who is still here) has worked on the code, then you have a single point of failure risk. You might want to set a threshold of two or three people who have worked on a repo if its important enough to keep using.
I've seen people purposefully making a new person make changes and deploy (under guidance) to build experience in the process, and reduce reliance on other people. 

*Are all developers the same? What about cross functional teams and different components?*

So, while there is talk of "full stack" developers, from what I've seen there's still a lot of Person X is the Front end developer, and Person Y is the database person. Depending on the complexity, similar to key person risk, you may want to break down the types of code into skill sets or capabilities. 
For example, you may  have people who know the front end code, but have zero working knowledge of the database.

Argh, it looks there there are secrets in this orphaned repo?!?
Secrets seems to be a challenge for most organistaions, everyone needs them and where do you put them (which should NOT be code, but it happens). Secrets is a whole other topic, but we have a few interesting cases in orphaned repos. 
Just say you find secrets, there's a few things to confirm:
- Are they active and do we use them? (Also, have we had a breach from a clone?)
- If they are active, we should probably roll or change the credentials if still used
- Are they old and inactive (like old SSL keys an certificates) 
- Do we try to clean the repo before archiving? Or just delete it?

Do we archive or delete?
This question has quite a subjective response depending on companies archiving and destruction processes to be followed. 
One company I've worked with I thought had a good approach:
- Archive it (so still accessible) by moving location in Git when criteria is met such as
  No commits in 1-2 years. 
- See if anyone asks for it in 12 months after it's archived.
- Figure out after 12 months if you need to retain in archive for X years or can delete
They were a big company with thousands of developers, so this is an ongoing process that needs to be run. Even in smaller longer running teams, these rules probably still apply. 

Improve focus when there's less things to think about
From my security side, when you start working with an organisation, their developers and code, there can be a lot to take in. Often with hundreds to thousands of repos, you ask, what's actually being used in production, and what's old code. 
There's three parts to this focus:
- Product owner - what do I need to maintain?
- Security - Is this code or application secure?
- Developer - what am I working on? 

Anecdotally, from discussions with Devolopment managers and even security assurance,  you want to know what's being used and remove what's not needed. I think less clutter means less wasted time. 

Keep in mind that developers change all the time, the current vibe is 20-30% yearly churn. A new developer has to come into an environment has to understand what's important, so removing old unused orphaned repos I think helps. From an application security or assurance point of view, remove the noise to focus on the important stuff. 

*Future investigation and next steps*

So far, we haven't talked about opensource libraries and code in the software supply chain. There are simple ways of just looking at library versions to see if they look unmaintained or orphaned. 

For opensource, I think it's safe to call something unmaintained and probably orphaned if a library or repo you use hasn't been updated in 12 month. I'd be worried without seeing activity after three months. 

We've talked about orphans and hinted about maintenance status. Orphans is about having no people who know the code, but aging or maintenance status is a different indicator worth looking at. I'd suggest that looking at what hasn't been maintained recently but still being used is good activity to identify hotspots. 

So, asking for a friend, what are you doing about orphaned repos?

If you're using kospex, there is a command to do this:
> kospex orphans

This command works on the configurable active developers concept (e.g. You're active if you've committed in X days)

Check out the docs for [kospex](/kospex) 
