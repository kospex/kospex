""" A module to query the dep.dev API and parse package manager files"""
import datetime
import os
import sys
import re
import json
import csv
import urllib
from urllib.parse import urlparse
import dateutil.parser
import requests
from prettytable import PrettyTable
import kospex_schema as KospexSchema

class KospexDependencies:
    """kospex database query functionality"""

    def __init__(self, kospex_db=None, kospex_query=None):
        # Initialize the kospex environment
        self.kospex_db = kospex_db
        self.kospex_query = kospex_query
        #KospexUtils.init()
        #self.kospex_db = Database(KospexUtils.get_kospex_db_path())

    def deps_dev(self,package_type,package_name,version):
        """ Query the Deps.dev API for a package and version"""
        base_url = "https://api.deps.dev/v3alpha/systems"
        encoded_name = urllib.parse.quote(package_name, safe='')
        url = f"{base_url}/{package_type}/packages/{encoded_name}/versions/{version}"
        #/v3alpha/systems/{versionKey.system}/packages/{versionKey.name}/versions/{versionKey.version}
        #https://api.deps.dev/v3alpha/systems/pypi/packages/requests/versions/2.31.0
        # links -> which has a SOURCE_REPO label should be the git

        data = None
        if self.kospex_query:
            content = self.kospex_query.url_request(url)
            if content:
                data = json.loads(content)
        else:
            req = requests.get(url, timeout=10)
            if req.status_code == 200:
                data = req.json()

        return data

    def get_url_json(self, url, timeout=10, cache=True):
        """ Get the contents of a URL, use the query cache if we can """
        data = None

        if self.kospex_query and cache:
            content = self.kospex_query.url_request(url)
            data = json.loads(content)
        else:
            req = requests.get(url, timeout=timeout)
            if req.status_code == 200:
                data = req.json()
        return data


    def get_pypi_package_info(self,package):
        """ Get the latest version of a package from PyPI """
        url = f"https://pypi.org/pypi/{package}/json"

        data = None
        if self.kospex_query:
            content = self.kospex_query.url_request(url)
            data = json.loads(content)
        else:
            req = requests.get(url, timeout=10)
            if req.status_code == 200:
                data = req.json()
            #return req.json()["info"]["version"]

        if data:
            return data["info"]["version"]
        else:
            return None

    def extract_github_url(self,s):
        """
        Extracts and returns the GitHub URL from a string.
        :param s: A string containing a GitHub repository URL.
        :return: The GitHub URL, if found; otherwise, None.
        """
        match = re.search(r'https://github\.com/[a-zA-Z0-9-_.]+/[a-zA-Z0-9-_.]+', s)
        return match.group(0) if match else None

    def extract_repo_path(self,url):
        """
        Extracts the 'guessed' repo path from a URL, regardless of the base.
        This is used to determine the repo path for GitHub, GitLab, and Bitbucket.
        Currently only tested with GitHub.
        :param url: A string containing the URL.
        :return: The repository path.
        """
        parsed_url = urlparse(url)
        path = parsed_url.path

        if path.endswith('.git'):
            path = path[:-4]  # Remove '.git' if present

        return path

    def is_valid_pypi_package_declaration(self,s):
        """
        Returns True if the string s follows the pattern <package_name>==<version_number>,
        for packges declared in a requirements.txt (or similar named) file.
        :param s: A string representing the package and version.
        :return: True if the pattern matches, False otherwise.
        """
        # TODO - fix for >= scenarios
        pattern = r'^[a-zA-Z0-9-_]+==\d+(\.\d+)*$'
        return re.match(pattern, s) is not None

    def get_table_field_names(self):
        """ Return the field names for the CSV table """
        return ["package_name", "package_version", "days_ago",
                "published_at", "source_repo", "advisories", "default", "versions_behind"]

    def get_cli_pretty_table(self):
        """ Return a pretty table for the CLI """
        table = PrettyTable()
        #table.field_names = ["package", "version", "days_ago",
        #                "published_at", "source_repo", "advisories", "default"]
        table.field_names = self.get_table_field_names()
        table.align["package_name"] = "l"
        table.align["package_version"] = "r"
        table.align["days_ago"] = "r"
        table.align["published_at"] = "l"
        table.align["source_repo"] = "l"
        table.align["advisories"] = "r"
        table.align["default"] = "r"
        table.align["versions_behind"] = "r"
        return table

    def is_npm_package(self,filename):
        """
        Check if a filename looks like a package.json file, 
        including lock and other common variants.
    
        Args:
        filename (str): The filename to check.

        Returns:
        bool: True if it looks like a package.json variant, False otherwise.
        """
        # Regular expression to match package.json and its variants
        pattern = r'^(.*-)?package(-lock)?(\.test|\.prod|\.dev)?\.json$'
        basefile = os.path.basename(filename)
        #pattern = r'^package\.json$'
        return bool(re.match(pattern, basefile, re.IGNORECASE))

    def assess(self, filename, results_file=None, repo_info={}):
        """ Using deps.dev to assess and provide a summary of the package manager file """

        basefile = os.path.basename(filename)
        print(f"Assessing {basefile}")
        if self.is_npm_package(filename):
            print(f"Found npm package file: {basefile}")
            self.npm_assess(filename,results_file=results_file,repo_info=repo_info)
        elif basefile != "requirements.txt":
            print(f"Only requirements.txt files are supported.  Found {basefile}")
            sys.exit(1)
        else:
            self.pypi_assess(filename,results_file=results_file,repo_info=repo_info)

    def get_values_array(self, input_dict, keys, default_value):
        """ return an array of values from a dictionary, using the keys provided"""
        return [input_dict.get(key, default_value) for key in keys]

    def get_source_repo_info(self, package_info):
        """ Get the source repo from a deps.dev package info dictionary """
        source_repo = None

        if package_info.get("links") is not None:
            for link in package_info.get("links"):
                if link.get("label") == "SOURCE_REPO":
                    source_repo = link.get("url")

        return source_repo

    def get_advisories_count(self, package_info):
        """ Return the number of security advisories from a deps.dev package info dictionary """
        advisories = package_info.get("advisoryKeys")
        if advisories:
            return len(advisories)
        else:
            return 0

    def write_csv(self, filename, table_rows, headers):
        """ Utility method for writing a CSV file. """
        with open(filename, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(table_rows)

    def get_npm_dependency_dict(self, item, data):
        """ parse a dependency details from the package.json json structure into a dictionary """
        details = {}
        today = datetime.datetime.now(datetime.timezone.utc)

        details['package'] = item
        details['version'] = data['dependencies'][item]
        details['semantic'] = ""
        # Handling semantic versioning
        # https://dev.to/typescripttv/understanding-npm-versioning-3hn4
        if "~" in details['version']:
            # Using the tilde symbol, we would only receive updates at the patch level
            details['version'] = details['version'].replace("~","")
            details['semantic'] = "~"
        if "^" in details['version']:
            # The caret symbol indicates that npm should restrict upgrades to 
            # patch or minor level updates
            details['version'] = details['version'].replace("^","")
            details['semantic'] = "^"

        print(f"Checking {item} version {details['version']}")
        deps_info = self.deps_dev("npm",item,details['version'])
        pub_date = deps_info.get("publishedAt")
        if pub_date:
            pub_date = dateutil.parser.isoparse(deps_info.get("publishedAt"))
            diff = today - pub_date
            details["days_ago"] = diff.days
        details["published_at"] = deps_info.get("publishedAt")
        details["default"] = deps_info.get("isDefault")
        # TODO - need to parse the source repo to create proper links for NPM .. looks 
        # a little dirty with actual Git urls and not https to Github
        details["source_repo"] = self.get_source_repo_info(deps_info)
        details["advisories"] = self.get_advisories_count(deps_info)

        # Get the versions behind info
        days_info = self.get_versions_behind("npm",item,details['version'])
        details['versions_behind'] = days_info.get("versions_behind","Unknown")

        # TODO - this is a hacky way of duplicating the keys needed
        details['package_name'] = details['package']
        details['package_version'] = details['version']

        return details

    def npm_assess(self, filename, results_file=None,repo_info={}):
        """ Using deps.dev to assess and provide a summary of a 
            npm package.json compatible file """

        #today = datetime.datetime.now(datetime.timezone.utc)
        table = self.get_cli_pretty_table()
        #records = []
        table_rows = []

        # We'll have no idea what encoding the file is in
        # TODO - check if there is a sensible default encoding for npm files
        # pylint: disable=unspecified-encoding
        npm_file = open(filename)
        data = json.load(npm_file)

        for item in data['dependencies']:
            details = self.get_npm_dependency_dict(item,data)
            print(details)
            table_rows.append(self.get_values_array(details, self.get_table_field_names(), '-'))

        for item in data['devDependencies']:
            print(f"Checking dev {item} version {data['devDependencies'][item]}")

        table.add_rows(table_rows)
        print(table)

        if results_file:
            self.write_csv(results_file, table_rows, self.get_table_field_names())

    def pypi_assess(self, filename,results_file=None,repo_info=None,store=True):
        """ Using deps.dev to assess and provide a summary of a 
            pip / PyPi requirements.txt compatible file """

        today = datetime.datetime.now(datetime.timezone.utc)
        table = self.get_cli_pretty_table()
        records = []
        table_rows = []

        #results_file = results_file if results_file else None

        # TODO - write functions to parse based on file type
        # (e.g. requirements.txt, pom.xml, package.json, etc.)
        with open(filename, 'r', encoding="utf-8") as pmf:
            for line in pmf.readlines():
                row = {}
                if repo_info:
                    row = repo_info.copy()
                row['package_type'] = 'PyPi'
                
                # Skip comments
                if line.startswith('#'):
                    continue

                # Skip blank lines
                if line.strip() == '':
                    continue

                # Skip lines that don't follow the pattern <package_name>==<version_number>
                if not self.is_valid_pypi_package_declaration(line.strip()):
                    url = self.extract_github_url(line.strip())
                    repo_path = self.extract_repo_path(url) if url else None
                    if url:
                        if repo_path:
                            row['package'] = repo_path
                        else:
                            row['package'] = repo_path
                    else:
                        row['package'] = line.strip()

                    row["version"] = "Unknown"
                    row["days_ago"] = "Unknown"
                    row["published_at"] = "Unknown"
                    row["source_repo"] = url
                    row["advisories"] = "unknown"
                    row["default"] = "unknown"
                    records.append(row)
                    continue

                # Looks like a valid line, let's continue and parse it

                package = line.split('==')[0]
                version = line.split('==')[1].strip()
                row['package_name'] = package
                row['package_version'] = version

                info = self.deps_dev("pypi",package,version)
                print(f"{package} : {version}")
                if info is None:
                    print(f"Could not find {package} in deps.dev")
                    continue
                pub_date = info.get("publishedAt")
                if pub_date:
                    pub_date = dateutil.parser.isoparse(info.get("publishedAt"))
                    diff = today - pub_date
                    row['days_ago'] = diff.days
                else:
                    row['days_ago'] = "Unknown"
                row['published_at'] = pub_date

                source_repo = ""
                if info.get("links") is not None:
                    for link in info.get("links"):
                        if link.get("label") == "SOURCE_REPO":
                            source_repo = link.get("url")

                row['source_repo'] = source_repo

                advisories = info.get("advisoryKeys")
                if advisories:
                    row['advisories'] = len(advisories)
                else:
                    row['advisories'] = 0

                row['default'] = info.get("isDefault")

                days_info = self.get_versions_behind("PyPi",package,version)
                row['versions_behind'] = days_info.get("versions_behind","Unknown")

                table_rows.append(self.get_values_array(row, self.get_table_field_names(), '-'))

                records.append(row)

        table.add_rows(table_rows)

        if results_file:
            print(f"Writing results to {results_file}")
            with open(results_file, 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(table.field_names)
                writer.writerows(table_rows)

        if store:
            #self.store_results(records)
            #print(records)
            # TODO - see if there is better way of excluding this key
            for r in records:
                r.pop("days_ago",None)
            self.kospex_db.table(KospexSchema.TBL_DEPENDENCY_DATA).upsert_all(
                records,pk=['_repo_id', 'hash', "file_path",
                            "package_type","package_name","package_version"])
            print("Stored results to DB (should have)")

        print(table)

    def find_dependency_files(self,directory):
        """ Find all dependency files (package managers) in a directory and its subdirectories."""
        # Map of filename to its package manager
        package_files = {
            'requirements.txt': 'PyPi',
            'Pipfile': 'PyPi (Pipenv)',
            'Pipfile.lock': 'PyPi (Pipenv)',
            'setup.py': 'PyPi',
            'pyproject.toml': 'PyPi (Poetry/Flit/etc.)',
            'Gemfile': 'RubyGems',
            'Gemfile.lock': 'RubyGems',
            'package.json': 'npm',
            'yarn.lock': 'Yarn',
            'composer.json': 'Composer',
            'composer.lock': 'Composer',
            'pom.xml': 'Maven',
            'build.gradle': 'Gradle',
            'build.gradle.kts': 'Gradle',
            'Cargo.toml': 'Cargo (Rust)',
            'Cargo.lock': 'Cargo (Rust)',
            'go.mod': 'Go Modules',
            'go.sum': 'Go Modules',
            'Podfile': 'CocoaPods',
            'Podfile.lock': 'CocoaPods',
        }

        detected_files = []

        # Use os.walk() to recursively search through directory and its subdirectories
        for root, dirs, files in os.walk(directory):
            for filename in files:
                if filename in package_files:
                    # Append the full path to the file, and its type
                    detected_files.append((os.path.join(root, filename), package_files[filename]))

        return detected_files

    def get_depsdev_info(self,package_manager, package_name, version):

        # Define the base URL for deps.dev API
        base_url = f"https://deps.dev/_/s/{package_manager}/p/{package_name}/v/{version}"

        # Make a request to get package details
        response = requests.get(base_url)
        if response.status_code != 200:
            raise Exception(f"Failed to get package details: {response.text}")

        # Extract the package data
        data = response.json()
        return data

    def version_fuzzy_match(self, from_config, from_depsdev):
        """ Compare two version strings and return True if they are a match, False otherwise. """

        # If the versions are exactly the same, return True
        if from_config == from_depsdev:
            return True

        # change the version strings to lists of integers to remove 0 padding, 
        # which doesn't always match properly e.g. 2022.07.13 != 2022.7.13        
        parts = from_config.split(".")
        #parts = [str(int(part)) for part in parts]
        cleaned_parts = [part.lstrip("0") for part in parts]
        ver_string = ".".join(cleaned_parts)

        if ver_string == from_depsdev:
            return True

        if len(parts) == 2:
            # Only 1 period like 3.4 we might need a .0 on the end to make it 3.4.0
            ver_string = ".".join([ver_string, "0"])
            if ver_string == from_depsdev:
                return True

        return False

    def get_versions_behind(self,package_manager, package_name, version):
        """ Use Deps.Dev API to get the versions behing the used version"""
        # Define the base URL for deps.dev API
        base_url = "https://api.deps.dev/v3alpha/systems"
        encoded_name = urllib.parse.quote(package_name, safe='')
        #url = f"{base_url}/{package_manager}/packages/{encoded_name}/versions/{version}"
        # The following is the correct URL to get all versions
        url = f"{base_url}/{package_manager}/packages/{encoded_name}"
        #package_url = f"https://deps.dev/_/s/{package_manager}/p/{encoded_name}/v/{version}"

        data = self.get_url_json(url)
        versions = data.get('versions', [])
        # We will need to sort the list, since the API returns the versions in a weird order
        #print(json.dumps(versions, indent=2))
        #print(len(versions))

        # Handle the case where there is no publishedAt key, we need this key to sort later
        for v in versions:
            if not v.get("publishedAt"):
                v['publishedAt'] = ''
                # TODO - log when we have a missing publishedAt
                # Perhaps track this metadata
                #print(f"missing publishedAt for {v['versionKey']['version']}")
            #print(v['versionKey']['version'])
            #print(v.get("publishedAt"))

        sorted_list = sorted(versions, key=lambda x: x['publishedAt'], reverse=True)
        # Find the 'default' install version
        #default_version = data.get("defaultVersion")
        #print(json.dumps(sorted_list, indent=2))

        details = {}
        details['versions'] = len(sorted_list)

        keys_before_default = 0
        versions_behind = 0
        found_default = False

        for release in sorted_list:

            if release["isDefault"]:
                print(f"default {release['versionKey']['version']}")
                found_default = True

            if self.version_fuzzy_match(version, release['versionKey']['version']):
                print(f"Found version {version}")
                break

            #if release['versionKey']['version'] == version:
            #    print(f"Found version {version}")
            #    break

            #print(release['versionKey']['version'])

            if not found_default:
                keys_before_default += 1
            else:
                versions_behind += 1

        print(f"Keys before default: {keys_before_default}")
        print(f"Versions between: {versions_behind}")
        details['versions_before_default'] = keys_before_default
        details['versions_behind'] = versions_behind

        return details
