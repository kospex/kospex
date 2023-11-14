""" A module to query the dep.dev API and parse package manager files"""
import datetime
import os
import sys
import re
import json
import csv
from urllib.parse import urlparse
import dateutil.parser
import requests
from prettytable import PrettyTable

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
        #url = f"https://api.github.com/repos/{package_name}/releases/latest"
        base_url = "https://api.deps.dev/v3alpha/systems"
        url = f"{base_url}/{package_type}/packages/{package_name}/versions/{version}"
        #/v3alpha/systems/{versionKey.system}/packages/{versionKey.name}/versions/{versionKey.version}
        #https://api.deps.dev/v3alpha/systems/pypi/packages/requests/versions/2.31.0
        # links -> which has a SOURCE_REPO label should be the git

        data = None
        if self.kospex_query:
            content = self.kospex_query.url_request(url)
            data = json.loads(content)
        else:
            req = requests.get(url, timeout=10)
            if req.status_code == 200:
                data = req.json()

        return data
        #req = requests.get(url)
        #if req.status_code == 200:
        #    return req.json()
        #else:
        #    return None

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

    def get_latest_version(self,package):
        """ Get the latest version of a package from PyPI """
        url = f"https://pypi.org/pypi/{package}/json"
        req = requests.get(url, timeout=10)
        if req.status_code == 200:
            return req.json()["info"]["version"]
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

    def get_cli_pretty_table(self):
        """ Return a pretty table for the CLI """
        table = PrettyTable()
        table.field_names = ["package", "version", "days_ago",
                        "published_at", "source_repo", "advisories", "default"]    
        table.align["package"] = "l"
        table.align["version"] = "r"
        table.align["days_ago"] = "r"
        table.align["published_at"] = "l"
        table.align["source_repo"] = "l"
        table.align["advisories"] = "r"
        table.align["default"] = "r"
        return table

    def assess(self, filename, results_file=None):
        """ Using deps.dev to assess and provide a summary of the package manager file """

        basefile = os.path.basename(filename)
        if basefile != "requirements.txt":
            print(f"Only requirements.txt files are supported.  Found {basefile}")
            sys.exit(1)
        else:
            self.pypi_assess(filename,results_file=results_file)

    def pypi_assess(self, filename,results_file=None):
        """ Using deps.dev to assess and provide a summary of a 
            pip / PyPi requirements.txt compatible file """

        #packages = {}
        today = datetime.datetime.now(datetime.timezone.utc)
        table = self.get_cli_pretty_table()
        records = []
        table_rows = []

        #results_file = results_file if results_file else None

        # TODO - write functions to parse based on file type
        # (e.g. requirements.txt, pom.xml, packge.json, etc.)
        with open(filename, 'r', encoding="utf-8") as pmf:
            for line in pmf.readlines():

                entry = []
                row = {}

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
                            entry.append(repo_path)
                            row['package'] = repo_path
                        else:
                            entry.append(url)
                            row['package'] = repo_path
                    else:
                        entry.append(line.strip())
                        row['package'] = line.strip()

                    entry.extend(["Unknown", "Unknown", "Unknown",
                                url, "Unknown", "Unknown"])
                    row["version"] = "Unknown"
                    row["days_ago"] = "Unknown"
                    row["published_at"] = "Unknown"
                    row["source_repo"] = url
                    row["advisories"] = "unknown"
                    row["default"] = "unknown"
                    #table.add_row(entry)
                    table_rows.append(entry)
                    records.append(row)
                    continue

                # Looks like a valid line, let's continue and parse it

                package = line.split('==')[0]
                version = line.split('==')[1].strip()
                entry.append(package)
                row['package'] = package
                entry.append(version)
                row['version'] = version

                #packages[package] = version
                info = self.deps_dev("pypi",package,version)
                print(f"{package} : {version}")
                if info is None:
                    print(f"Could not find {package} in deps.dev")

                    continue
                #print(info.get("publishedAt"))
                pub_date = info.get("publishedAt")
                if pub_date:
                    pub_date = dateutil.parser.isoparse(info.get("publishedAt"))
                    diff = today - pub_date
                    #print(f"days ago: {diff.days}")
                    entry.append(diff.days)
                else:
                    entry.append("Unknown")
                entry.append(info.get("publishedAt"))

                source_repo = ""
                if info.get("links") is not None:
                    for link in info.get("links"):
                        if link.get("label") == "SOURCE_REPO":
                            #print(link.get("url"))
                            source_repo = link.get("url")
                entry.append(source_repo)
                advisories = info.get("advisoryKeys")
                if advisories:
                    entry.append(len(advisories))
                else:
                    entry.append(0)

                entry.append(info.get("isDefault"))

                #print()
                #table.add_row(entry)
                table_rows.append(entry)
                records.append(row)

        table.add_rows(table_rows)

        if results_file:
            print(f"Writing results to {results_file}")
            with open(results_file, 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(table.field_names)
                writer.writerows(table_rows)

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


#today = datetime.datetime.now(datetime.timezone.utc)
