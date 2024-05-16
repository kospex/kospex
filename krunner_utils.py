""" Helper functions for krunner """
import os
import json
import re
import yaml
from prettytable import PrettyTable

def extract_grep_parameters(input_string):
    """Extracts a grep command string into filename, line_number and string_contents. """
    # Split the input string at colons
    parts = input_string.split(":")
    if len(parts) < 3:
        return None
    # Extract the first two parameters and the rest of the string
    #first_param = parts[0].lstrip("./") if len(parts) > 0 else ""
    first_param = parts[0].lstrip(".").lstrip("/") if len(parts) > 0 else ""
    # the strip should remove the ./ in filenames
    second_param = parts[1] if len(parts) > 1 else ""
    rest_of_string = ":".join(parts[2:]) if len(parts) > 2 else ""

    # Return the extracted parameters and the rest of the string as an array
    return [first_param, second_param, rest_of_string]

def find_dockerfiles_in_repos(repo_dirs):
    """Find docker files in a list of repo directories"""

    results_files = []

    for rp in repo_dirs:

        # Walk through the directory
        for root, dirs, files in os.walk(rp):

            for file in files:
                # Check if the filename matches case insensitive options
                lower_name = file.lower()
                if lower_name == "dockerfile":
                    #print("Dockerfile: " + os.path.join(d, file))
                    results_files.append(os.path.join(root, file))

                if lower_name.startswith("docker-compose"):
                    #print("Dockerfile: " + os.path.join(d, file))
                    results_files.append(os.path.join(root, file))

    return results_files

def load_gitleaks(filename):
    """Load gitleaks output from a file"""
    f = open(filename, "r")
    data = json.load(f)
    f.close()
    return data

def load_trufflehog(filename):
    """Load trufflehog output from a file"""
    f = open(filename, "r")
    lines = f.readlines()
    data = []
    for line in lines:
        try:
            data.append(json.loads(line))
        except json.decoder.JSONDecodeError:
            print(f"Error loading trufflehog output in {filename}")
            print(line)
    f.close()
    return data

def get_secrets_heatmap_table(heatmap_data):
    """Get a heatmap table from secrets heatmap data"""

    table = PrettyTable()
    table.field_names = ["Repo ID", "Total", "trufflehog", "gitleaks"]
    table.align["Repo ID"] = "l"
    table.align["Total"] = "r"
    table.align["trufflehog"] = "r"
    table.align["gitleaks"] = "r"
    table.sortby = "Total"
    table.reversesort = True

    for repo_id, data in heatmap_data.items():
        if not data.get("gitleaks"):
            data["gitleaks"] = 0
        if not data.get("trufflehog"):
            data["trufflehog"] = 0
        data["Total"] = data["trufflehog"] + data["gitleaks"]

        table.add_row((repo_id, data["Total"], data["trufflehog"], data["gitleaks"]))

    return table

def find_docker_from_statements(filename):
    """ Find FROM statements in Docker files."""
    # Define a regular expression pattern to match Docker FROM statements.
    # This pattern captures the base image and the type (if present) after 'as'
    pattern = re.compile(r'^FROM\s+([\w.:/-]+)(?:\s+as\s+(\w+))?')

    results = []

    with open(filename, 'r') as file:
        for line_number, line in enumerate(file, start=1):
            match = pattern.match(line)
            if match:
                # Extract the base image and the type (if present)
                base_image = match.group(1)
                type_key = match.group(2) if match.group(2) is not None else ""
                results.append({
                    'filename': filename,
                    'line_number': line_number,
                    'base_image': base_image,
                    'type': type_key,
                })
    return results

def find_docker_compose_images(filename):
    """ Find images in Docker Compose files."""

    conf = {}
    with open(filename) as f:
        conf = yaml.safe_load(f)

    images = []

    if conf:
        for service in conf['services']:
            image = {}
            image["base_image"] = conf['services'][service].get("image","")
            image["type"] = service
            image["filename"] = filename
            images.append(image)

    return images

def get_docker_images(records):
    """
    Extract the base image and type from a list of records."""

    record_images = []

    for rec in records:

        record_mod = rec.copy()
        record_mod['base_image'] = ""
        record_mod['type'] = ""

        filename = rec.get('file_path',"")
        basename = os.path.basename(filename)
        from_images = []

        if basename and basename.startswith("Dockerfile"):
            from_images = find_docker_from_statements(rec['file_path'])

        elif basename and basename.startswith("docker-compose"):
            from_images = find_docker_compose_images(rec['file_path'])

        if from_images and len(from_images) > 0:
            for img in from_images:
                record_mod = rec.copy()
                record_mod['base_image'] = img['base_image']
                record_mod['type'] = img['type']
                record_images.append(record_mod)

        else:
            record_images.append(record_mod)

    return record_images
    