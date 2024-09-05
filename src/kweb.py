#!/usr/bin/env python3
""" This is the local development web server to view the Kospex database. """
from os.path import basename
import sys
from flask import Flask, render_template, request, jsonify
from kospex_query import KospexQuery
import kospex_web as KospexWeb
import kospex_utils as KospexUtils

app = Flask(__name__)

@app.route('/')
def index():
    """ Serve up the summary home page """
    data = KospexQuery().summary()
    return render_template('index.html', **data)

@app.route('/developers/active/<repo_id>')
def active_developers(repo_id):
    """ Developer info page. """
    data = KospexQuery().summary(days=90,repo_id=repo_id)
    results = KospexQuery().active_devs_by_repo(repo_id)
    print(results)
    return render_template('developers.html',data=data, authors=results)

@app.route('/developers/')
def developers():
    """ Developer info page. """
    author_email = request.args.get('author_email')
    download = request.args.get('download')
    days = request.args.get('days',None)
    devs = KospexQuery().authors(days=days)

    if author_email:
        print(author_email)
        # Github uses +, which get interpreted as a " " in the URL.
        author_email = author_email.replace(" ","+")
        repo_list = KospexQuery().repos_by_author(author_email)
        techs = KospexQuery().author_tech(author_email=author_email)
        labels = []
        datapoints = []

        count = 0
        for tech in techs:
            labels.append(tech['_ext'])
            datapoints.append(tech['commits'])
            count += 1
            if count > 10:
                break

        return render_template('developer_view.html', repos=repo_list,
                               tech=techs, author_email=author_email,
                               labels=labels, datapoints=datapoints)

    elif download:
        return KospexWeb.download_csv(devs)
    else:
        data = KospexQuery().summary(days=days)
        return render_template('developers.html', authors=devs, data=data )

@app.route('/landscape/')
def landscape():
    """ Serve up the technology landscape metadata """
    kospex = KospexQuery()
    repo_id = request.args.get('repo_id')
    org_key = request.args.get('org_key')
    data = kospex.tech_landscape(org_key=org_key,repo_id=repo_id)
    download = request.args.get('download')
    if download:
        # TODO - need to pass in the query params for orgs or repo
        return KospexWeb.download_csv(data)
    else:
        return render_template('landscape.html', data=data, org_key=org_key)

@app.route('/repos/')
def repos():
    """ display repo information. """

    repo_id = request.args.get('repo_id')
    kospex = KospexQuery()
    org_key = request.args.get('org_key')
    server = request.args.get('server')
    page = {}
    # TODO - validate params
    techs = None
    # Maintenance ranges
    ranges = None

    page['repo_id'] = repo_id

    data = []

    if org_key:
        parts = org_key.split("~")
        if len(parts) == 2:
            page['git_server'] = parts[0]
            page['git_owner'] = parts[1]
            techs = kospex.tech_landscape(org_key=org_key)
            ranges = kospex.commit_ranges2(org_key=org_key)
            print(kospex.commit_ranges2(repo_id=repo_id,org_key=org_key))
    elif server:
        page['git_server'] = server

    # The repos method handles null values for parameters
    data = kospex.repos(org_key=org_key,server=server)
    active_devs = kospex.active_devs()
    for row in data:
        row['active_devs'] = active_devs.get(row['_repo_id'],0)

    developers = kospex.developers(org_key=org_key)
    developer_status = KospexUtils.repo_stats(developers,"last_commit")

    return render_template('repos.html',data=data,
        page=page, ranges=ranges, techs=techs, developer_status=developer_status)

@app.route('/servers/')
def servers():
    """ display Git server information. """

    kquery = KospexQuery()
    data = kquery.server_summary()
    print(data)

    return render_template('servers.html',data=data)

@app.route('/observations/')
def observations():
    """ display observation information. """
    kquery = KospexQuery()
    repo_id = request.args.get('repo_id')
    observation_key = request.args.get('observation_key')

    if observation_key:
        # We should have an observation key and a repo_id for this to work
        print("observation_key",observation_key)
        return render_template('observations_repo_key.html',
                               data=kquery.observations_summary(repo_id=repo_id,observation_key=observation_key),
                               observation_key=observation_key,repo_id=repo_id)

    elif repo_id:
        print("repo_id",repo_id)

        return render_template('observations_repo.html',
                               data=kquery.observations_summary(repo_id=repo_id),
                               repo_id=repo_id)
    else:
        return render_template('observations.html',data=kquery.observations_summary())

@app.route('/orgs/')
def orgs():
    """ display repo information. """

    org = request.args.get('org')

    kospex = KospexQuery()

    git_orgs = kospex.orgs()
    active_devs = kospex.active_devs(org=True)
    print(active_developers)

    for row in git_orgs:
        row['active_devs'] = active_devs.get(row['org_key'],0)

    return render_template('orgs.html',data=git_orgs)

@app.route('/repo/<repo_id>')
def repo(repo_id):
    """ display repo information. """
    kospex = KospexQuery()
    #data = kospex.repos()
    commit_ranges = kospex.commit_ranges(repo_id)
    print(commit_ranges)
    email_domains = kospex.email_domains(repo_id=repo_id)
    #summary = kospex.author_summary(repo_id)
    summary=kospex.author_summary(repo_id)
    techs = kospex.tech_landscape(repo_id=repo_id)

    developers = kospex.developers(repo_id=repo_id)
    developer_status = KospexUtils.repo_stats(developers,"last_commit")

    # TODO - make generic function for radar graph (in developer view too)
    labels = []
    datapoints = []
    count = 0
    for tech in techs:
        labels.append(tech['Language'])
        datapoints.append(tech['count'])
        count += 1
        if count > 10:
            break

    return render_template('repo_view.html',
                           repo_id=repo_id,
                           ranges=commit_ranges,
                           email_domains=email_domains,
                           landscape = techs,
                           developer_status=developer_status,
                           labels=labels,datapoints=datapoints,
                           summary=summary)

@app.route('/tech/<tech>')
def repo_with_tech(tech):
    """ Show repos with the given tech. """
    #print(tech)
    repo_id = request.args.get('repo_id')
    kospex = KospexQuery()
    template = "repos.html"
    if repo_id:
        repos_with_tech = kospex.repo_files(tech,repo_id=repo_id)
        template = "repo_files.html"
    else:
        repos_with_tech = kospex.repos_with_tech(tech)

    return render_template(template,data=repos_with_tech)



@app.route('/commits/')
def commits():
    """ display Git commits information. """
    repo_id = request.args.get('repo_id',"")
    author_email = request.args.get('author_email')
    commiter_email = request.args.get('committer_email')
    print(author_email)
    data = KospexQuery().commits(limit=100,repo_id=repo_id,
                                 author_email=author_email, committer_email=commiter_email)
    return render_template('commits.html', commits=data, repo_id=repo_id)

@app.route('/hotspots/<repo_id>')
def hotspots(repo_id):
    """ display Git commits information. """
    data = KospexQuery().hotspots(repo_id=repo_id)
    return render_template('hotspots.html', data=data)

@app.route('/meta/author-domains')
def author_domains():
    """ display repo information. """
    kospex = KospexQuery()
    email_domains = kospex.email_domains()
    return render_template('meta-author-domains.html',email_domains=email_domains)

# Error Handling Routes

@app.errorhandler(404)
def page_not_found(e):
    """ Serve up the 404 page """
    return render_template('404.html'), 404

# Experimental and Development Routes

@app.route('/bootstrap/')
def bootstrap():
    """ bootstrap 5 dev playground. """
    return render_template('bootstrap5.html')

@app.route('/tech-change/')
def tech_change():
    """ Radars for tech change. """
    labels = [ "Java", "Go", "JavaScript", "Python", "Kotlin" ]
    return render_template('tech-change.html', labels=labels)

@app.route('/metadata/')
def metadata():
    """ Metadata about the kospex DB. """
    details = {}
    return render_template('metadata.html')

@app.route('/graph', defaults={'org_key': None})
@app.route('/graph/', defaults={'org_key': None})
@app.route('/graph/<org_key>')
def graph(org_key):
    """
    Metadata about the kospex DB.
    """
    author_email = request.args.get('author_email')
    if author_email:
        # This is a weird old skool http thing
        # where spaces were represented by + signs
        author_email = author_email.replace(" ","+")
    repo_id = request.args.get('repo_id')


    if repo_id:
        org_key = f"?repo_id={repo_id}"
    elif author_email:
        org_key = f"?author_email={author_email}"

    #if not org_key:
    #    org_key = f"?author_email={author_email}"

    return render_template('graph.html',org_key=org_key)

@app.route('/org-graph', defaults={'org_key': None})
@app.route('/org-graph/', defaults={'org_key': None})
@app.route('/org-graph/<org_key>')
def org_graph(org_key):
    """
    Return JSON data for the force directed graph.

    """
    ### MVP

    repo_id = request.args.get('repo_id')
    print(f"org_key: {org_key} repo_id: {repo_id}")

    org_info = []
    if org_key:
        org_info = KospexQuery().get_graph_info(org_key=org_key)
    elif repo_id:
        org_info = KospexQuery().get_repo_files_graph_info(repo_id=repo_id)
    else:
        author_email = request.args.get('author_email')
        author_email = author_email.replace(" ","+")
        org_info = KospexQuery().get_graph_info(author_email=author_email)

    #org_info = KospexQuery().get_graph_info(org_key=org_key)
    #print(org_info)
    dev_lookup = {}
    repo_lookup = {}
    file_lookup = {}
    links = []
    nodes = []

    for element in org_info:

        if element['author'] not in dev_lookup:
            dev_lookup[element['author']] = { "id": element['author'],
                                             "group": 1,
                                             "label": KospexUtils.extract_github_username(element['author']),
                                             "info": element['author'],
                                             "repos": 1 }
        else:
            dev_lookup[element['author']]['repos'] += 1


        if repo_id:
            # We're handling files not repos
            if element['file_path'] not in file_lookup:
                file_lookup[element['file_path']] = { "id": element['file_path'],
                                                "group": 2,
                                                "label": basename(element['file_path']),
                                                "info": element['file_path'] }

        elif element['_repo_id'] not in repo_lookup:
            repo_lookup[element['_repo_id']] = { "id": element['_repo_id'],
                                                "group": 2,
                                                "label": element['_git_repo'],
                                                "info": element['_repo_id'] }

        link_key = "_repo_id"
        if repo_id:
            link_key = "file_path"

        links.append({"source": element['author'],
                      "target": element[link_key],
                      "commits": element['commits']})

    for element in dev_lookup:
        nodes.append(dev_lookup[element])

    for element in repo_lookup:
        nodes.append(repo_lookup[element])

    for element in file_lookup:
        nodes.append(file_lookup[element])

    data = {
            "nodes": [
                { "id": "Dev1", "group": 1, "info": "Developer 1 info" },
                { "id": "Dev2", "group": 1, "info": "Developer 2 info" },
                { "id": "Repo1", "group": 2, "info": "Repository 1 info" },
                { "id": "Repo2", "group": 2, "info": "Repository 2 info" },
                { "id": "Repo3", "group": 2, "info": "Repository 3 info" }
            ],
            "links": [
                { "source": "Dev1", "target": "Repo1", "commits": 50 },
                { "source": "Dev1", "target": "Repo2", "commits": 30 },
                { "source": "Dev2", "target": "Repo1", "commits": 20 },
                { "source": "Dev2", "target": "Repo3", "commits": 40 },
                { "source": "Dev3", "target": "Repo2", "commits": 60 },
                { "source": "Dev3", "target": "Repo3", "commits": 10 }
            ]
        }

    data["nodes"] = nodes
    data["links"] = links

    return data

def kweb():
    """ Run the web server. """
    if len(sys.argv) > 1:
        if sys.argv[1] == "-debug":
            print("\n#\nRunning in DEBUG mode.\n#\n\n")
            app.run(debug=True)
        else:
            exit("Unknown option, try -debug.")
    else:
        print("\n#\nRunning in NON debug mode.\n#\n\n")
        app.run()

if __name__ == "__main__":

    if len(sys.argv) > 1:
        if sys.argv[1] == "-debug":
            print("\n#\nRunning in DEBUG mode.\n#\n\n")
            app.run(debug=True)
        else:
            exit("Unknown option, try -debug.")
    else:
        print("\n#\nRunning in NON debug mode.\n#\n\n")
        app.run()
