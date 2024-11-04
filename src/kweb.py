#!/usr/bin/env python3
""" This is the local development web server to view the Kospex database. """
from os.path import basename
import sys
import base64
from flask import Flask, render_template, request, jsonify
from jinja2 import TemplateNotFound
from kospex_bitbucket import KospexBitbucket
from kospex_query import KospexQuery
import kospex_web as KospexWeb
import kospex_utils as KospexUtils

app = Flask(__name__)

# @app.route('/')
# def index():
#     """ Serve up the summary home page """
#     data = KospexQuery().summary()
#     return render_template('index.html', **data)

@app.route('/summary/', defaults={'id': None})
@app.route('/summary/<id>')
@app.route('/', defaults={'id': None})
def summary(id):
    """ Serve up the summary home page """
    params = KospexWeb.get_id_params(id)
    devs = KospexQuery().developers(**params)

    dev_stats = KospexUtils.count_key_occurrences(devs,"status")
    dev_percentages = KospexUtils.convert_to_percentage(dev_stats)

    total = sum(dev_stats.values())
    dev_stats["total"] = total

    result = {}
    for name, percentage in dev_percentages.items():
        if percentage:
            dev_stats[f"{name}_percentage"] = round(percentage)
        result[name] = round(100 * (percentage / 100)) + 40

    repos = KospexQuery().repos(**params)
    repo_stats = KospexUtils.count_key_occurrences(repos,"status")

    repo_sizes = {}
    repo_percentages = KospexUtils.convert_to_percentage(repo_stats)
    # Do the total after percentages are calculated
    total = sum(repo_stats.values())
    repo_stats["total"] = total

    for name, percentage in repo_percentages.items():
        if percentage:
            repo_stats[f"{name}_percentage"] = round(percentage)
        repo_sizes[name] = round(100 * (percentage / 100)) + 40

    return render_template('summary.html', developers=dev_stats,
        data_size=result, repos=repo_stats, repo_sizes=repo_sizes,id=params)

@app.route('/help', defaults={'id': None})
@app.route('/help/', defaults={'id': None})
@app.route('/help/<id>')
def help(id):
    """ Serve up the help pages """
    page = "404"
    if id:
        # Check that the id is safe to use
        valid_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
        if set(id).issubset(valid_chars):
            print("looks legit")
            page = f"help/{id}"
        else:
            page = "404"
    else:
        page = "help/index"

    try:
        return render_template(f'{page}.html')
    except TemplateNotFound:
        return render_template('404.html'), 404


@app.route('/developers/active/<repo_id>')
def active_developers(repo_id):
    """ Developer info page. """
    data = KospexQuery().summary(days=90,repo_id=repo_id)
    results = KospexQuery().active_devs_by_repo(repo_id)
    print(results)
    return render_template('developers.html',data=data, authors=results)

@app.route('/developer', defaults={'id': None})
@app.route('/developer/', defaults={'id': None})
@app.route('/developer/<id>')
def dev(id):
    """
    View a developers details
    """
    # WIP - migrating singled developer out of /developers/ route

    author_email = request.args.get('author_email')

    if id:
        author_email = KospexUtils.decode_base64(id)
        #print("--")
        #base64_bytes = id.encode('ascii')
        #message_bytes = base64.b64decode(base64_bytes)
        #author_email = message_bytes.decode('ascii')

    print(author_email)
    # Github uses +, which get interpreted as a " " in the URL.
    if author_email:
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


@app.route('/developers/')
def developers():
    """ Developer info page. """
    author_email = request.args.get('author_email')
    download = request.args.get('download')
    days = request.args.get('days',None)
    org_key = request.args.get('org_key')
    devs = KospexQuery().authors(days=days,org_key=org_key)

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


@app.route('/landscape', defaults={'id': None})
@app.route('/landscape/', defaults={'id': None})
@app.route('/landscape/<id>')
def landscape(id):
    """ Serve up the technology landscape metadata """
    kospex = KospexQuery()

    params = KospexWeb.get_id_params(id)
    repo_id = request.args.get('repo_id') or params.get("repo_id")
    org_key = request.args.get('org_key') or params.get("org_key")
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

    developers = kospex.developers(org_key=org_key,server=server)
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
    """ Metadata about the kospex DB and repos. """
    data = KospexQuery().summary()
    return render_template('metadata.html', **data)



@app.route('/bubble/<id>')
def bubble(id):
    """
    Display a bubble chart of developers in a repo
    or the repos for an org_key
    or the repos for a given user

    Show the developers for a repo_id
    /bubble/<repo_id>
    Show the developers for an org_key
    /bubble/<org_key>
    Show the developers for a git_server
    /bubble/<git_server>
    Show repos for a developer with a base64 encoded email
    /bubble/EMAIL_B64

    Show repo view of an org_key
    /bubble/repo/<org_key>

    """

    link_url = ""

    if KospexUtils.parse_repo_id(id):
        link_url = f"repo/{id}"
    elif KospexUtils.is_base64(id):
        link_url = f"dev/{id}"
    else:
        link_url = f"{id}"

    #if "~" in repo_id:
    #    link_url = f"repo/{repo_id}"
    #else:
    #    print("maybe a dev?")
    #    link_url = f"dev/{repo_id}"

    return render_template('bubble.html',link_url=link_url)

@app.route('/graph-api/<id>')
def graph_api(id):

    org_info = []
    data = {
            "nodes": [],
            "links": []
    }
    links = []
    nodes = []

    if KospexUtils.parse_org_key(id):
        org_info = KospexQuery().get_graph_info(org_key=id)

    elif KospexUtils.parse_repo_id(id):
        org_info = KospexQuery().get_graph_info(repo_id=id)

    elif KospexUtils.is_base64(id):
        email = KospexUtils.decode_base64(id)
        org_info = KospexQuery().get_graph_info(author_email=email,
            by_repo=True)

    elif focus:

        if focus == "repo":
            org_info = KospexQuery().get_graph_info(repo_id=repo_id)
        else:
            org_info = KospexQuery().get_graph_info(author_email=author_email,
                by_repo=True)
            print("Unknown focus")
            print(org_info)

        print(f"in focus, with focus: {focus}")

    elif repo_id:
        org_info = KospexQuery().get_repo_files_graph_info(repo_id=repo_id)
        #org_info = KospexQuery().get_graph_info(org_key=org_key)

    elif author_email:
        # This should be the b64 parameter that's decoded
        org_info = KospexQuery().get_graph_info(author_email=author_email)

    elif git_server:
        org_info = KospexQuery().get_graph_info(git_server=git_server)

    data["nodes"] = nodes
    data["links"] = links

    return data


@app.route('/graph', defaults={'org_key': None})
@app.route('/graph/', defaults={'org_key': None})
@app.route('/graph/<org_key>')
def graph(org_key):
    """
    Force directed graphs for data in the Kospex DB.
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

    return render_template('graph.html',org_key=org_key)

@app.route('/org-graph', defaults={'org_key': None, "focus": None})
@app.route('/org-graph/', defaults={'org_key': None, "focus": None})
@app.route('/org-graph/<org_key>',defaults={"focus": None})
@app.route('/org-graph/<focus>/<org_key>')
def org_graph(focus,org_key):
    """
    Return JSON data for the force directed graph.

    """
    ### MVP

    repo_id = request.args.get('repo_id')
    author_email = None
    git_server = None
    # TODO we're hacking around if we're actualy passed a repo_id and not an org_key

    if org_key:
        repo_parts = KospexUtils.parse_repo_id(org_key)
        if repo_parts:
            repo_id = org_key
            org_key = None
        elif KospexUtils.parse_org_key(org_key):
            print(f"looks like {org_key} is an org_key")

        elif KospexUtils.is_base64(org_key):
            # Doesn't look like an org_key
            # Possibly an author email
            base64_bytes = org_key.encode('ascii')
            message_bytes = base64.b64decode(base64_bytes)
            decoded = message_bytes.decode('ascii')
            # Rough check to see if it's an email
            if "@" in decoded:
                org_key = None
                author_email = decoded
        else:
            # Possibly just a git server
            git_server = org_key
            org_key = None


    print(f"org_key: {org_key}\nrepo_id: {repo_id}\nfocus: {focus}")

    org_info = []

    if org_key:
        org_info = KospexQuery().get_graph_info(org_key=org_key)

    elif focus:

        if focus == "repo":
            org_info = KospexQuery().get_graph_info(repo_id=repo_id)
        else:
            org_info = KospexQuery().get_graph_info(author_email=author_email,
                by_repo=True)
            print("Unknown focus")
            print(org_info)


        print(f"in focus, with focus: {focus}")

    elif repo_id:
        org_info = KospexQuery().get_repo_files_graph_info(repo_id=repo_id)
        #org_info = KospexQuery().get_graph_info(org_key=org_key)

    elif author_email:
        # This should be the b64 parameter that's decoded
        org_info = KospexQuery().get_graph_info(author_email=author_email)

    elif git_server:
        org_info = KospexQuery().get_graph_info(git_server=git_server)

    else:
        author_email = request.args.get('author_email')
        author_email = author_email.replace(" ","+")
        org_info = KospexQuery().get_graph_info(author_email=author_email)

    dev_lookup = {}
    repo_lookup = {}
    file_lookup = {}
    links = []
    nodes = []

    #print(org_info)

    for element in org_info:

        last_commit = element.get("last_commit")
        status = KospexUtils.development_status(KospexUtils.days_ago(last_commit))

        group_numbers = {}
        group_numbers['Active'] = 1
        group_numbers['Aging'] = 2
        group_numbers['Stale'] = 3
        group_numbers['Unmaintained'] = 4

        group = 1
        if org_key:
            # we only have 1 group, and that's developers
            group = 1
            # in graph, group is used to link between
        else:
            group = group_numbers.get(status,4)

        b64_email = KospexUtils.encode_base64(element.get('author'))

        if element['author'] not in dev_lookup:
            dev_lookup[element['author']] = { "id": element['author'],
                                             "id_b64": b64_email,
                                             "group": group,
                                             "label": KospexUtils.extract_github_username(element['author']),
                                             "info": element['author'],
                                             "commits": element.get("commits"),
                                             "status_group": group_numbers.get(status,4),
                                             "status": status,
                                             "last_commit": last_commit,
                                             "repos": 1 }
        else:
            dev_lookup[element['author']]['repos'] += 1


        if repo_id and not focus:
            # We're handling files not repos
            file_path = element.get('file_path')
            if element.get('file_path') not in file_lookup:
                file_lookup[element['file_path']] = { "id": element['file_path'],
                                                "group": 2,
                                                "label": basename(element['file_path']),
                                                "info": element['file_path'] }

        elif element['_repo_id'] not in repo_lookup:
            repo_lookup[element['_repo_id']] = { "id": element['_repo_id'],
                                                "group": 2,
                                                "commits": element.get("commits",0),
                                                "status_group": group_numbers.get(status,4),
                                                "status": status,
                                                "link": f"/repo/{element.get('_repo_id')}",
                                                "last_commit": last_commit,
                                                "label": element['_git_repo'],
                                                "info": element['_repo_id'] }

        link_key = "_repo_id"
        if repo_id:
            link_key = "file_path"

        links.append({"source": element['author'],
                      "target": element.get(link_key),
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
            print("WARNING: LISTENING ON 0.0.0.0\n")
            app.run(host="0.0.0.0",debug=True)
        else:
            exit("Unknown option, try -debug.")
    else:
        print("\n#\nRunning in NON debug mode.\n#\n\n")
        app.run()

if __name__ == "__main__":

    if len(sys.argv) > 1:
        if sys.argv[1] == "-debug":
            print("\n#\nRunning in DEBUG mode.\n#\n\n")
            print("WARNING: LISTENING ON 0.0.0.0\n")
            app.run(host="0.0.0.0",debug=True)
        else:
            exit("Unknown option, try -debug.")
    else:
        print("\n#\nRunning in NON debug mode.\n#\n\n")
        app.run()
