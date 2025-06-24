""" A module to query the dep.dev API and parse package manager files"""
import datetime
import os
import sys
import re
import json
import csv
import urllib
import time
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from typing import List, Dict, Optional
import dateutil.parser
import requests
from prettytable import PrettyTable
import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_git import KospexGit

class KospexDependencies:
    """kospex database query functionality"""

    def __init__(self, kospex_db=None, kospex_query=None):
        # Initialize the kospex environment
        self.kospex_db = kospex_db
        self.kospex_query = kospex_query
        self.git = KospexGit()
        #self.kospex_db = Database(KospexUtils.get_kospex_db_path())
        # The following will be the results from the list of dependencies from the assess command
        self.dependencies = []

    def extract_purl(self, purl):
        # Extract the purl from the given URL
        # Example: purl = "pkg:pypi/requests@2.31.0"
        # Extract the package type, name, and version from the purl
        # Return a dictionary with the extracted information
        package_only = purl.split(":")[1]
        package_type = package_only.split("/")[0]
        full_package = package_only.split("/")[1]
        package_name = full_package.split("@")[0]
        version = full_package.split("@")[1]
        return {
            "ecosystem": package_type,
            "package_name": package_name,
            "package_version": version
        }

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
            else:
                print(f"Error: {req.status_code} {req.text}")

        return data

    def deps_dev_package(self,package_type,package_name):
        """
        Query the deps.dev API for a package and get all version history
        """
        base_url = "https://api.deps.dev/v3alpha/systems"
        encoded_name = urllib.parse.quote(package_name, safe='')
        url = f"{base_url}/{package_type}/packages/{encoded_name}"
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
            else:
                print(f"Error: {req.status_code} {req.text}")

        return data



    def get_url_json(self, url, timeout=10, cache=True):
        """ Get the contents of a URL, use the query cache if we can """
        data = None

        if self.kospex_query and cache:
            if content := self.kospex_query.url_request(url):
                data = json.loads(content)
        else:
            req = requests.get(url, timeout=timeout)
            if req.status_code == 200:
                data = req.json()

        return data


    def get_pypi_package_info(self,package,version: Optional[str] = None):
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
        for packages declared in a requirements.txt (or similar named) file.
        :param s: A string representing the package and version.
        :return: True if the pattern matches, False otherwise.
        """
        # TODO - fix for >= scenarios
        pattern = r'^[a-zA-Z0-9-_]+==\d+(\.\d+)*$'
        return re.match(pattern, s) is not None

    def get_table_field_names(self):
        """ Return the field names for the CSV table """
        return ["package_name", "package_version", "versions_behind", "days_ago",
                "published_at", "default", "advisories", "malware",  "source_repo", "authors"]

    def get_cli_pretty_table(self):
        """ Return a pretty table for the CLI """
        table = PrettyTable()

        table.field_names = self.get_table_field_names()
        table.align["package_name"] = "l"
        table.align["package_version"] = "r"
        table.align["days_ago"] = "r"
        table.align["published_at"] = "l"
        table.align["source_repo"] = "l"
        table.align["advisories"] = "r"
        table.align["malware"] = "r"
        table.align["default"] = "r"
        table.align["versions_behind"] = "r"
        table.align["authors"] = "r"

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

    def is_nuget_package(self,filename):
        """
        Check if a filename looks like a .csproj file,
        including lock and other common variants.

        Args:
        filename (str): The filename to check.

        Returns:
        bool: True if it looks like a .csproj variant, packages.config, etc False otherwise.
        """
        # Regular expression to match package.json and its variants
        basefile = os.path.basename(filename)
        pattern = r'^.*\.csproj$'
        csproj = bool(re.match(pattern, basefile, re.IGNORECASE))
        # TODO - handle packages.config also, and maybe other .proj file variants
        return csproj

    def is_pip_requirements_file(self,filename):
        """
        Check if the given filename matches common patterns for Python pip requirements files.

        Args:
        filename (str): The filename to check.

        Returns:
        bool: True if the filename matches common patterns for pip requirements files, False otherwise.
        """
        # Regular expression to match common patterns for pip requirements files
        pattern = re.compile(r'^requirements(-\w+)?\.txt$', re.IGNORECASE)
        return bool(pattern.match(filename))

    #def assess(self, filename, results_file=None, repo_info=None, print_table=False):
    def assess(self, filename, **kwargs):
        """ Using deps.dev to assess and provide a summary of the package manager file.
        Args:
        filename (str): The filename to assess.
        dev_deps : If we want to include dev dependencies
        """
        results_file = kwargs.get('results_file',None)
        repo_info = kwargs.get('repo_info',None)
        print_table = kwargs.get('print_table',False)
        dev_deps = kwargs.get('dev_deps',False)
        save = kwargs.get('save',False)

        git_details = {}
        package_type = "Unknown"

        abs_file_path = os.path.abspath(filename)
        base_dir = os.path.dirname(abs_file_path)
        git_base = KospexUtils.find_git_base(base_dir)

        if git_base:
            self.git.set_repo(git_base)
            git_details = self.git.add_git_to_dict(git_details)
            git_details['hash'] = self.git.get_current_hash()
            file_path = os.path.relpath(abs_file_path, git_base)
            git_details['file_path'] = file_path
        else:
            # Probably not in a Git directory, but we can still do SCA
            file_path = filename

        basefile = os.path.basename(filename)

        # print(f"base: {basefile}")
        # print(f"a: {abs_file_path}\nbd: {base_dir}\ngit_base: {git_base}\n")
        # print(f"rel_path: {file_path}")

        # return array of package records
        #
        results = []

        if basefile == "go.mod":
            print(f"Found Go mod package file: {basefile}")
            package_type = "Go module"
            results = self.gomod_assess(filename,results_file=results_file,repo_info=repo_info)

        elif self.is_npm_package(filename):
            print(f"Found npm package file: {basefile}")
            package_type = "npm"
            results = self.npm_assess(filename,results_file=results_file,
                            repo_info=repo_info,dev_deps=dev_deps)

        elif self.is_nuget_package(filename):
            print(f"Found nuget package file: {basefile}")
            package_type = "nuget"
            self.nuget_assess(filename,results_file=results_file,repo_info=repo_info)

        elif self.is_pip_requirements_file(basefile):
            print(f"Found pip requirements file: {basefile}")
            package_type = "pypi"
            results = self.pypi_assess(filename,results_file=results_file,
                             repo_info=repo_info, print_table=print_table)

        else:
            print(f"Unknown or unsupported package manager file found {basefile}")

        if results:
            for dep in results:
                if publishedAt := dep.get('published_at', None):
                    dep["days_ago"] = KospexUtils.days_ago(publishedAt)

        if save and git_details:
            import pprint
            pprint.PrettyPrinter(indent=4).pprint(results)
            # TODO - see if there is better way of excluding this key
            for r in results:
                r.pop("days_ago",None)
                r.pop("authors",None)
                r.update(git_details)
                r["package_type"] = package_type
                r["latest"] = 1

            self.kospex_db.table(KospexSchema.TBL_DEPENDENCY_DATA).upsert_all(
                 results,pk=['_repo_id', 'hash', "file_path",
                             "package_type","package_name","package_version"])

            # print("Stored results to DB (should have)")

        self.dependencies = results

        return self.dependencies

    def save_dependencies(self,git_details=None):
        """
        Save the dependencies to the kospex database.

        For the _repo_id and file_path, set latest to 0

        """

        # Set the latest to 0 based on the criteria above


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

    def get_npm_dependency_dict(self, item, data, dependency_key=None):
        """ parse a dependency details from the package.json json structure into a dictionary """
        details = {}
        today = datetime.datetime.now(datetime.timezone.utc)

        if not dependency_key:
            dependency_key = "dependencies"

        details['package'] = item
        details['version'] = data[dependency_key][item]
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

        record = self.depsdev_record("npm",item,details['version'])
        record['semantic'] = details['semantic']

        #print(f"Checking {item} version {details['version']}")
        #deps_info = self.deps_dev("npm",item,details['version'])
        #pub_date = deps_info.get("publishedAt")
        #if pub_date:
        #    pub_date = dateutil.parser.isoparse(deps_info.get("publishedAt"))
        #    diff = today - pub_date
        #    details["days_ago"] = diff.days
        #details["published_at"] = deps_info.get("publishedAt")
        #details["default"] = deps_info.get("isDefault")
        # TODO - need to parse the source repo to create proper links for NPM .. looks
        # a little dirty with actual Git urls and not https to Github
        #details["source_repo"] = self.get_source_repo_info(deps_info)
        #details["source_repo"] = KospexUtils.extract_git_url(self.get_source_repo_info(deps_info))

        #details["advisories"] = self.get_advisories_count(deps_info)

        # Get the versions behind info
        #days_info = self.get_versions_behind("npm",item,details['version'])
        #details['versions_behind'] = days_info.get("versions_behind","Unknown")

        # TODO - this is a hacky way of duplicating the keys needed
        #details['package_name'] = details['package']
        #details['package_version'] = details['version']

        #return details
        return record

    def depsdev_record(self, package_type, package_name, package_version):
        """ Convert a deps.dev package info record into a dictionary with other metadata """

        details = {}
        details['package_name'] = package_name
        details['package_version'] = package_version
        details['package_type'] = package_type

        today = datetime.datetime.now(datetime.timezone.utc)
        # TODO - Handle bad package names
        # TODO - Handle 404 errors (most likely due to bad package name)
        if package_version:
            deps_info = self.deps_dev(package_type,package_name,package_version)

        # If we don't get any info back, we'll just return an empty dictionary
        if not deps_info:
            #details['source_repo'] = "Unknown, maybe internal library"
            return details

        pub_date = None
        if deps_info:
            pub_date = deps_info.get("publishedAt")

        if pub_date:
            pub_date = dateutil.parser.isoparse(deps_info.get("publishedAt"))
            diff = today - pub_date
            details["days_ago"] = diff.days

        details["published_at"] = deps_info.get("publishedAt")
        details["default"] = deps_info.get("isDefault")
        # TODO - need to parse the source repo to create proper links for NPM .. looks
        # a little dirty with actual Git urls and not https to Github
        details["source_repo"] = KospexUtils.extract_git_url(self.get_source_repo_info(deps_info))
        details["advisories"] = self.get_advisories_count(deps_info)

        # Get the versions behind info
        if package_version:
            days_info = self.get_versions_behind(package_type,package_name,package_version)
            details['versions_behind'] = days_info.get("versions_behind","Unknown")

        # TODO - this is a hacky way of duplicating the keys needed
        #details['package_name'] = package_name
        #details['package_version'] = package_version
        details['authors'] = None
        if details['source_repo']:
            details['authors'] = self.get_repo_authors(details['source_repo'])

        return details

    def npm_assess(self, filename, results_file=None, repo_info=None, dev_deps=None):
        """ Using deps.dev to assess and provide a summary of a
            npm package.json compatible file """

        params = locals()
        print(params)

        #today = datetime.datetime.now(datetime.timezone.utc)
        table = self.get_cli_pretty_table()
        results = []
        table_rows = []
        if repo_info is None:
            repo_info = {}

        # We'll have no idea what encoding the file is in
        # TODO - check if there is a sensible default encoding for npm files
        # pylint: disable=unspecified-encoding
        npm_file = open(filename)
        data = json.load(npm_file)



        #for item in data.get('dependencies'):
        if "dependencies" in data:
            for item in data.get('dependencies'):
                details = self.get_npm_dependency_dict(item,data)
                #print(details)
                results.append(details)
                print(item)
                table_rows.append(self.get_values_array(details, self.get_table_field_names(), '-'))

        if 'devDependencies' in data:
            for item in data['devDependencies']:
                if dev_deps:
                    details = self.get_npm_dependency_dict(item,data,dependency_key="devDependencies")
                    results.append(details)
                    print(item)
                    table_rows.append(self.get_values_array(details, self.get_table_field_names(), '-'))
                else:
                    print(f"Skipping check for dev {item} version {data['devDependencies'][item]}")

        table.add_rows(table_rows)
        print(table)

        if results_file:
            self.write_csv(results_file, table_rows, self.get_table_field_names())

        return results

    def get_pypi_source_repo(self, package_name):
        """ Get the source repo for a PyPi package """
        url = f"https://pypi.org/pypi/{package_name}/json"

        data = None
        if self.kospex_query:
            if content := self.kospex_query.url_request(url):
                data = json.loads(content)
        else:
            req = requests.get(url, timeout=10)
            if req.status_code == 200:
                data = req.json()

        if data:
            repo = None
            info = data.get("info")
            purls = info.get("project_urls")
            if purls:
                repo = purls.get("Source")
                if not repo:
                    repo = purls.get("Source Code")
            # TODO check description for source repo if the above fails
            return repo

        else:
            return None


    def parse_pypi_package_declaration(self, package_declaration):
        """ Parse a PyPi package declaration into a dictionary """
        # TODO - need to double check the version specifiers as described in:
        # https://packaging.python.org/en/latest/specifications/version-specifiers/
        # They are claiming a lot of different ways to specify versions
        package = {}
        version_spec = None
        single_specifier = True

        match = re.match(r'([a-zA-Z0-9_-]+)(.+)', package_declaration)

        if match:
            package['package_name'] = match.group(1)
            package['package_version'] = match.group(2)

        if ',' in package_declaration:
            single_specifier = False
            package['version_type'] = 'multiple'
        elif '>=' in package_declaration:
            version_spec = '>='
        elif '~=' in package_declaration:
            version_spec = '~='
        elif '==' in package_declaration:
            version_spec = '=='
        else:
            print(f"Unknown version type in {package_declaration}")
            return None

        if single_specifier:
            package['package_name'] = package_declaration.split(version_spec)[0]
            package['package_version'] = package_declaration.split(version_spec)[1]
            package['version_type'] = version_spec

        return package

    def pypi_assess(self, filename,results_file=None,repo_info=None,
                    store=False, dependency_authors=None, print_table=False):
        """ Using deps.dev to assess and provide a summary of a
            pip / PyPi requirements.txt compatible file """

        #today = datetime.datetime.now(datetime.timezone.utc)
        table = self.get_cli_pretty_table()

        records = []
        table_rows = []

        #results_file = results_file if results_file else None
        # (e.g. requirements.txt, pom.xml, package.json, etc.)
        with open(filename, 'r', encoding="utf-8") as pmf:
            for line in pmf.readlines():
                print(f"Checking {line.strip()}")
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
                # Or other valid PyPi version specifiers (e.g. >=, ~=, etc.)
                package_declaration = self.parse_pypi_package_declaration(line.strip())
                if not package_declaration:
                #if not self.is_valid_pypi_package_declaration(line.strip()):
                    url = self.extract_github_url(line.strip())
                    repo_path = self.extract_repo_path(url) if url else None
                    if url:
                        if repo_path:
                            row['package_name'] = repo_path
                        else:
                            row['package_name'] = repo_path
                    else:
                        row['package_name'] = line.strip()

                    row["package_version"] = "Unknown"
                    row["days_ago"] = "Unknown"
                    row["published_at"] = "Unknown"
                    row["source_repo"] = url
                    row["advisories"] = "unknown"
                    row["default"] = "unknown"
                    records.append(row)
                    continue

                # Looks like a valid line, let's continue and parse it

                #package = line.split('==')[0]
                #version = line.split('==')[1].strip()
                package = package_declaration['package_name']
                version = package_declaration['package_version']
                row['package_name'] = package
                row['package_version'] = version

                record = {}
                #print(f"Checking {package} version |{version}|")
                # Check for multiple versions specifiers, we can't handle that
                if package_declaration.get('version_type') == 'multiple':
                    print(f"Skipping multiple version specifiers in {line.strip()}")
                    record['package_name'] = package_declaration['package_name']
                    #continue
                else:
                    record = self.depsdev_record("pypi",package,version)
                    if not record.get("source_repo"):
                        record["source_repo"] = self.get_pypi_source_repo(package)

                #record['authors'] = 0

                #if record["source_repo"] and dependency_authors:
                #    parts = self.git.extract_git_url_parts(record["source_repo"])
                #    if parts:
                #        repo_id = self.git.repo_id_from_url_parts(parts)
                        # TODO - Possibly need to query # of authors
                        # before this version publish date
                #        authors = self.kospex_query.authors_by_repo(repo_id)
                #        if authors:
                #            record['authors'] = len(authors)
                #record['authors'] = self.get_repo_authors(record["source_repo"])
                table_rows.append(self.get_values_array(record, self.get_table_field_names(), '-'))

                #records.append(row)
                records.append(record)

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

        if print_table:
            print(table)

        return records

    def print_dependencies_table(self, records,malware=None):
        table = self.get_cli_pretty_table()
        table_rows = []

        for dep in records:
            table_rows.append(self.get_values_array(dep, self.get_table_field_names(), '-'))

        table.add_rows(table_rows)
        print(table)

    def get_repo_authors(self, repo_url):
        """ Return the number of unique authors for a given repo that's been sync'ed """
        authors = 0
        # by default, we'll return None authors if we can't find any
        parts = self.git.extract_git_url_parts(repo_url)
        if parts:
            repo_id = self.git.repo_id_from_url_parts(parts)
            # TODO - Possibly need to query # of authors
            # before this version publish date
            author_list = self.kospex_query.authors_by_repo(repo_id)
            if author_list:
                authors = len(author_list)

        return authors

    def nuget_assess(self, filename, results_file=None, repo_info=None, store=True):
        """ Assess a nuget .cproj file """

        table = self.get_cli_pretty_table()
        table_rows = []
        result = []

        try:
            with open(filename, 'r') as xml_file:
                xml_data = xml_file.read()
            root = ET.fromstring(xml_data)
            # Find all PackageReference elements
            package_references = root.findall(".//PackageReference")
            # Extract the 'Include' and 'Version' attributes
            result = [ {"package_name": pkg.attrib["Include"],
                    "package_version": pkg.attrib["Version"]} for pkg in package_references]
            #print(result)

        except Exception as e:
            print(f"Error parsing {filename}: {e}")
            return False

        for pkg in result:
            #print(f"Checking {pkg['package_name']} version {pkg['package_version']}")
            rec = self.depsdev_record("NuGet",pkg['package_name'],pkg['package_version'])
            table_rows.append(self.get_values_array(rec, self.get_table_field_names(), '-'))

        table.add_rows(table_rows)
        print(table)
        #table_rows.append(self.get_values_array(details, self.get_table_field_names(), '-'))


    def find_dependency_files(self,directory):
        """ Find all dependency files (package managers) in a directory and its subdirectories."""
        # Map of filename to its package manager
        package_files = {
            'requirements.*.txt$': 'PyPi',
            'Pipfile': 'PyPi (Pipenv)',
            'Pipfile.lock': 'PyPi (Pipenv)',
            'setup.py': 'PyPi',
            'pyproject.toml': 'PyPi (Poetry/Flit/etc.)',
            'Gemfile': 'RubyGems',
            'Gemfile.lock': 'RubyGems',
            'package.*.json': 'npm',
            'yarn.lock': 'Yarn',
            'composer.json': 'Composer',
            'composer.lock': 'Composer',
            'pom.xml': 'Maven',
            'build.gradle': 'Gradle',
            'build.gradle.kts': 'Gradle',
            'Cargo.toml': 'Cargo (Rust)',
            'Cargo.lock': 'Cargo (Rust)',
            'go.mod$': 'Go Modules',
            'go.sum': 'Go Modules',
            'Podfile': 'CocoaPods',
            'Podfile.lock': 'CocoaPods',
        }

        detected_files = []

        regex_patterns = [re.compile(pattern) for pattern in package_files.keys()]

        # Use os.walk() to recursively search through directory and its subdirectories
        for root, dirs, files in os.walk(directory):
             # Exclude .git directory
            if '.git' in dirs:
                dirs.remove('.git')

            for filename in files:
                for pattern in regex_patterns:
                    if pattern.match(filename):
                        # Add matching file path to the list
                        # TODO - considering logging for debugging purposes
                        #print(f"Found package file {filename} in {root}")
                        detected_files.append(os.path.join(root, filename))
                        break  # No need to match other patterns if one has matched
                #if filename in package_files:
                #    # Append the full path to the file, and its type
                #    detected_files.append((os.path.join(root, filename), package_files[filename]))
                        # Check each file against the pattern

        return detected_files

    def get_depsdev_info(self,package_manager, package_name, version):
        """ Query deps.dev API for package details."""

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
                #print(f"default {release['versionKey']['version']}")
                found_default = True

            if self.version_fuzzy_match(version, release['versionKey']['version']):
                #print(f"Found version {version}")
                break

            if not found_default:
                keys_before_default += 1
            else:
                versions_behind += 1

        #print(f"Keys before default: {keys_before_default}")
        #print(f"Versions between: {versions_behind}")
        details['versions_before_default'] = keys_before_default
        details['versions_behind'] = versions_behind

        return details

    def gomod_assess(self, filename, results_file=None, repo_info=None):
        """ Using deps.dev to assess and provide a summary of a
            go mod compatible file """

        table = self.get_cli_pretty_table()
        table_rows = []
        if repo_info is None:
            repo_info = {}

        records = []

        deps = self.parse_go_mod_from_file(filename)

        for item in deps:
            if item['indirect'] is False:
                #details = self.get_depsdev_info('gomod', item['module'], item['version'])
                #self.depsdev_record(repo_info, details)
                record = self.depsdev_record("go",item['module'],item['version'])
                print(record)
                table_rows.append(self.get_values_array(record, self.get_table_field_names(), '-'))
                records.append(record)
                #print(item)

        #for item in data['dependencies']:
        #    details = self.get_npm_dependency_dict(item,data)
        #    #print(details)
        #    print(item)
        #    table_rows.append(self.get_values_array(details, self.get_table_field_names(), '-'))


        table.add_rows(table_rows)
        print(table)

        if results_file:
            self.write_csv(results_file, table_rows, self.get_table_field_names())

        return records


    def parse_go_mod_from_file(self,file_path):
        """ Parse the go.mod file and return the dependencies and their versions. """
        # Initialize an array to store the results
        results = []

        try:
            # Open the file and read the contents
            with open(file_path, 'r') as file:
                lines = file.readlines()

                # Flag to check if we're inside a require block
                in_require_block = False

                for line in lines:
                    # Trim leading and trailing whitespace
                    trimmed_line = line.strip()

                    # Check if we're entering a require block
                    if trimmed_line == 'require (':
                        in_require_block = True
                        continue  # Move to the next line

                    # Check if we're exiting a require block
                    if trimmed_line == ')' and in_require_block:
                        in_require_block = False
                        continue  # Move to the next line

                    # Parse the line if we're inside a require block
                    if in_require_block:
                        # Split the line into parts
                        parts = trimmed_line.split()

                        # Ensure the line has at least two parts: module and version
                        if len(parts) >= 2:
                            # Extract module and version
                            module, version = parts[0], parts[1]

                            # Check if the module is marked as indirect
                            indirect = 'indirect' in parts

                            # Append the information to the results array
                            results.append({
                                'module': module,
                                'version': version,
                                'indirect': indirect
                            })
        except FileNotFoundError:
            print(f"File {file_path} not found.")
        except Exception as e:
            print(f"An error occurred while reading the file: {e}")

        # Return the results array
        return results

    def check_malware(self, package_type,package_name, api_key):
        # Beta implementation of malicious packages API
        url = f"https://api.maliciouspackages.com/package/{package_type}/{package_name}"
        is_malware = False
        headers = {
                    "X-API-Key": api_key
                }
        try:
            time.sleep(1)
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                malware_result = response.json()
                is_malware = malware_result.get("malware", False)
                #result['malware'] = malware_result.get("malware", False)
            else:
                print(f"Error checking {package_name}: HTTP {response.status_code}")
                #result['malware'] = {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            print(f"Exception when checking {package_name}: {str(e)}")
                #result['malware'] = {"error": str(e)}
        # else:
        #     print(f"Skipping package with missing type or name: {package_type} and {package_name}")

        return is_malware

    def package_dependencies(self, package: str, version: str, ecosystem: str):
        """
        Lookup the package dependencies on deps.dev

        Args:
            package: Package name to lookup.
            version: Package version to lookup.
            ecosystem: Package ecosystem to lookup.
        """
        package = package
        version = version
        ecosystem = ecosystem
        #GET /v3/systems/{versionKey.system}/packages/{versionKey.name}/versions/{versionKey.version}:dependencies
        base_url = "https://api.deps.dev/v3/systems"
        encoded_name = urllib.parse.quote(package, safe='')
        url = f"{base_url}/{ecosystem}/packages/{encoded_name}/versions/{version}:dependencies"
        #https://api.deps.dev/v3alpha/systems/pypi/packages/requests/versions/2.31.0
        # links -> which has a SOURCE_REPO label should be the git

        print(url)

        data = self.get_url_json(url)
        # req = requests.get(url, timeout=10)
        # if req.status_code == 200:
        #     data = req.json()
        # # Need to handle HTTP error codes

        if data is None:
            return None

        nodes = data.get("nodes")
        for node in nodes:

            node["id"] = self.versionKey_id(node["versionKey"])

            system = node["versionKey"].get("system").lower()

            name = node["versionKey"].get("name").lower()
            node["name"] = name

            version = node["versionKey"].get("version").lower()
            node["version"] = version

            dep = self.deps_dev(system, name, version)
            node["publishedAt"] = dep.get("publishedAt")

            advisories = dep.get("advisoryKeys")
            num_advisories = len(advisories) if advisories else 0
            node["advisories"] = num_advisories

            versions = self.get_versions_behind(system, name, version)
            if versions:
                node["versions_behind"] = versions.get("versions_behind")


        return data

    def versionKey_id(self,versionKey):
        """
        Convert a deps.dev version key to a string
        """
        system = versionKey.get("system").lower()
        name = versionKey.get("name").lower()
        version = versionKey.get("version").lower()

        return ":".join([system, name, version])
