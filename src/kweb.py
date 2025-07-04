#!/usr/bin/env python3
""" This is the local development web server to view the Kospex database. """
from os.path import basename
import sys
import json

import pprint
from statistics import mean, median, mode, stdev, quantiles
from collections import Counter, OrderedDict
from flask import Flask, render_template, request, jsonify
from jinja2 import TemplateNotFound
from kospex_query import KospexQuery
import kospex_web as KospexWeb
import kospex_utils as KospexUtils
from kospex_core import Kospex, GitRepo
from kweb_help_service import HelpService
from kweb_graph_service import GraphService

app = Flask(__name__)

# Initialize services
help_service = HelpService()
graph_service = GraphService()

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
    """Serve up the help pages"""
    return help_service.render_help_page(id)


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

@app.route('/tenure/', defaults={'id': None})
@app.route('/tenure/<id>')
def tenure(id):
    """
    View developer tenure in for all, a server, org or a repo
    """

    # TODO - CHECK THIS FOR SECURITY
    params = KospexWeb.get_id_params(id)
    developers = KospexQuery().developers(**params)
    active_devs = []

    for entry in developers:
                entry['tenure_status'] = KospexUtils.get_status(entry['tenure'])

    for dev in developers:
        if "Active" == dev.get("status"):
            active_devs.append(dev)

    data = {}
    data['developers'] = len(developers)
    data['active_devs'] = len(active_devs)
    days_values = [entry['tenure'] for entry in developers]

    commit_stats = KospexQuery().get_activity_stats(params)
    print(commit_stats)
    data['days_active'] = commit_stats.get('days_active')
    data['years_active'] = commit_stats.get('years_active')
    data['repos'] = commit_stats.get('repos')
    data['commits'] = commit_stats.get('commits')

    data['max'] = round(max(days_values))
    data['mean'] = round(mean(days_values), 2)
    data['mode'] = round(mode(days_values), 2)
    data['median'] = round(median(days_values), 2)
    data['std_dev'] = round(stdev(days_values), 2)

    distribution = KospexUtils.get_status_distribution(developers)
    active_d = KospexUtils.get_status_distribution(active_devs)

    return render_template('tenure.html',data=data,
        distribution=distribution, developers=developers, active_distribution=active_d)


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

        github_handle = KospexUtils.extract_github_username(author_email)

        count = 0
        for tech in techs:
            labels.append(tech['_ext'])
            datapoints.append(tech['commits'])
            count += 1
            if count > 10:
                break

        return render_template('developer_view.html', repos=repo_list,
                               tech=techs, author_email=author_email,
                               labels=labels, github_handle=github_handle,
                               datapoints=datapoints)

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
        return render_template('landscape.html', data=data, org_key=org_key, id=id)

@app.route('/files/repo/', defaults={'repo_id': None})
@app.route('/files/repo/<repo_id>')
def repo_files(repo_id):
    """
    Show file metadata for a repo_id.
    """
    #data = KospexQuery().summary(days=90,repo_id=repo_id)
    #results = KospexQuery().active_devs_by_repo(repo_id)
    #print(results)
    data = None
    if repo_id:
        data = KospexQuery().repo_files(repo_id=repo_id)
    return render_template('files.html',data=data)

@app.route('/repos', defaults={'id': None})
@app.route('/repos/', defaults={'id': None})
@app.route('/repos/<id>')
def repos(id):
    """ display repo information. """

    params = KospexWeb.get_id_params(id)
    print(params)
    repo_id = request.args.get('repo_id') or params.get("repo_id")
    org_key = request.args.get('org_key') or params.get("org_key")
    server = request.args.get('server') or params.get("server")

    kospex = KospexQuery()

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

@app.route('/orgs', defaults={'server': None})
@app.route('/orgs/', defaults={'server': None})
@app.route('/orgs/<server>')
def orgs(server):
    """ display repo information. """
    org = request.args.get('org')
    params = KospexWeb.get_id_params(server)

    kospex = KospexQuery()
    git_orgs = kospex.orgs()
    active_devs = kospex.active_devs(org=True)

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

    return render_template(template,data=repos_with_tech,page = {})

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

@app.route('/collab/<repo_id>')
def collab(repo_id):
    """ display repo information. """
    kquery = KospexQuery()

    collabs = kquery.get_collabs(repo_id=repo_id)

    return render_template('collab.html', collabs=collabs)

# Error Handling Routes

@app.errorhandler(404)
def page_not_found(e):
    """ Serve up the 404 page """
    return render_template('404.html'), 404

# Experimental and Development Routes

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

@app.route('/osi/', defaults={'id': None})
@app.route('/osi/<id>')
def osi(id):
    """
    Functions around an Open Source Inventory
    """
    params = KospexWeb.get_id_params(id)
    deps = KospexQuery().get_dependency_files(id=params)
    for file in deps:
        file["days_ago"] = KospexUtils.days_ago(file.get("committer_when"))
        file["status"] = KospexUtils.development_status(file.get("days_ago"))
    file_number = len(deps)
    status = KospexUtils.repo_stats(deps,"committer_when")
    filenames = KospexUtils.filenames_by_repo_id(deps)
    #pprint.PrettyPrinter(indent=4).pprint(filenames)
    print(status)

    return render_template('osi.html',data=deps,
        file_number=file_number,
        dep_files=filenames,status=status)

@app.route('/dependencies/', defaults={'id': None})
@app.route('/dependencies/<id>')
def dependencies(id):
    """
    Display SCA information
    """
    params = KospexWeb.get_id_params(id)

    data = KospexQuery().get_dependencies(id=params)
    print(data)

    return render_template('dependencies.html',data=data)


@app.route('/orphans/', defaults={'id': None})
@app.route('/orphans/<id>')
def orphans(id):
    """
    Display orphan information
    """
    params = KospexWeb.get_id_params(id)

    data = KospexQuery().get_orphans(id=params)

    return render_template('orphans.html',data=data)

@app.route('/bubble/<id>', defaults={'template': "bubble"})
@app.route('/treemap/<id>', defaults={'template': "treemap"})
def bubble(id,template):
    """
    Display a bubble or treemap chart of developers in a repo
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

    html_template = f"{template}.html"

    return render_template(html_template,link_url=link_url,
        template=template,id=id)

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

    return render_template('graph2.html',org_key=org_key)

@app.route('/org-graph', defaults={'org_key': None, "focus": None})
@app.route('/org-graph/', defaults={'org_key': None, "focus": None})
@app.route('/org-graph/<org_key>',defaults={"focus": None})
@app.route('/org-graph/<focus>/<org_key>')
def org_graph(focus, org_key):
    """Return JSON data for the force directed graph."""
    return graph_service.get_graph_data(focus, org_key, request.args)

@app.route('/supply-chain/')
def supply_chain():
    """
    Display a bubble chart of package dependencies with their security status.
    Color coding:
    - Green: No advisories/malware and 0-2 versions behind
    - Yellow: No advisories/malware and 2-6 versions behind
    - Orange: Has advisories or older than 12 months
    - Red: Has malware or older than 2 years
    """
    package = request.args.get('package')
    data = None
    if package:
        system, package_name, package_version = package.split(":")
        if all([system, package_name, package_version]):
            # Process package data here
            kospex = Kospex()
            data = kospex.dependencies.package_dependencies(package=package_name,
                version=package_version, ecosystem=system)
    # Sample data - replace with actual data from your database
    #
    print(json.dumps(data,indent=3))

    if data is None:
        print("No data found")
        data = {
            "nodes": [
                {
                    "id": "package1",
                    "name": "Package 1",
                    "version": "1.0.0",
                    "publish_date": "2023-01-01",
                    "versions_behind": 0,
                    "advisories": 0,
                    "malware": False,
                    "size": 20
                },
                {
                    "id": "package2",
                    "name": "Package 2",
                    "version": "2.1.0",
                    "publish_date": "2023-06-01",
                    "versions_behind": 3,
                    "advisories": 0,
                    "malware": False,
                    "size": 15
                },
                {
                    "id": "package3",
                    "name": "Package 3",
                    "version": "0.5.0",
                    "publish_date": "2022-01-01",
                    "versions_behind": 5,
                    "advisories": 2,
                    "malware": False,
                    "size": 25
                },
                {
                    "id": "package4",
                    "name": "Package 4",
                    "version": "3.0.0",
                    "publish_date": "2021-01-01",
                    "versions_behind": 8,
                    "advisories": 0,
                    "malware": True,
                    "size": 30
                }
            ],
            "links": []
        }

    return render_template('supply_chain.html', data=data)

@app.route('/package-check/')
def package_check():
    """Display the package check page with drag and drop interface."""
    return render_template('package_check.html')

@app.route('/package-check/upload', methods=['POST'])
def package_check_upload():
    """Handle file upload and analyze dependencies."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    print("file contents:")
    print(file)

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Save the uploaded file temporarily
    import tempfile
    import os
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    try:
        # Analyze the file using KospexDependencies
        kospex = Kospex()
        results = kospex.dependencies.assess(temp_path)
        # Sort results by status
        pprint.PrettyPrinter(indent=4).pprint(results)

        # Clean up the temporary file
        os.remove(temp_path)
        os.rmdir(temp_dir)

        # Add status based on advisories and versions behind
        for item in results:
            if item.get('advisories', 0) > 0:
                item['status'] = 'Vulnerable'
            elif item.get('versions_behind', 0) > 6:
                item['status'] = 'Outdated'
            elif item.get('versions_behind', 0) > 2:
                item['status'] = 'Behind'
            else:
                item['status'] = 'Current'



        return jsonify(results)

    except Exception as e:
        # Clean up in case of error
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
        return jsonify({'error': str(e)}), 500

def kweb():
    """ Run the web server. """
    all_interfaces = False
    if "-all" in sys.argv:
        all_interfaces = True
        print("Found -all")

    if len(sys.argv) > 1:
        if "-debug" in sys.argv:
            print("\n#\nRunning in DEBUG mode.\n#\n\n")
            if all_interfaces:
                print("WARNING: LISTENING ON 0.0.0.0\n")
                app.run(host="0.0.0.0", debug=True, threaded=False)
            else:
                app.run(debug=True, threaded=False)
        else:
            exit("Unknown option, try -debug.")
    else:
        print("\n#\nRunning in NON debug, local mode.\n#\n\n")
        app.run(threaded=False)

if __name__ == "__main__":

    kweb()
