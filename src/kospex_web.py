""" Helper functions for kospex web UI """
import csv
import base64
from io import StringIO
from flask import make_response
import kospex_utils as KospexUtils

def download_csv(dict_data, filename=None):
    """ Download the given dict_data as a csv file """
    # We're going to assume this is a list of dictionaries from a database query
    # and we're going to use the first dictionary to get the header
    with StringIO() as output:
        # Assuming all dictionaries have the same keys,
        # so we can use the keys of the first dictionary for the header
        fieldnames = dict_data[0].keys()

        # Create a CSV writer object using the StringIO object as file-like
        writer = csv.DictWriter(output, fieldnames=fieldnames)

        # Write the header
        writer.writeheader()

        # Write the rows using the dictionary values
        for row_dict in dict_data:
            writer.writerow(row_dict)

        # Get the CSV string from the StringIO object
        csv_string = output.getvalue()

    # Set the output file name
    if not filename:
        filename = "download.csv"

    # Create a response, setting the Content-Type and Content-Disposition headers
    output = make_response(csv_string)
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"

    return output

def get_id_params(request_id,request_params=None):
    """
    Parse a request_id into either:
        repo_id
        org_key
        server
    if request_params is passed, we'll look at that, but the request_id takes precedence
    and return a dict with that key to be passed into a KospexQuery
    """
    params = {}

    # Only look at request_params if the request_id is None
    if request_id is None:
        print("ID is none")
        if request_params:
            if repo_id := request_params.get("repo_id"):
                params["repo_id"] = repo_id
            elif org_key := request_params.get("org_key"):
                params["org_key"] = org_key
            elif server := request_params.get("server"):
                params["server"] = server
            elif author_email := request_params.get("author_email"):
                # Web URLs sometimes translate + to a space
                # so so we need to replace ' ' with +
                # Like in github emails like 109855528+USERNAME@users.noreply.github.com
                params["author_email"] = author_email.replace(" ","+")

        return params

    # These methods return a Dict if parsed or None if not
    if KospexUtils.parse_org_key(request_id):
        params["org_key"] = request_id
    elif KospexUtils.parse_repo_id(request_id):
        params["repo_id"] = request_id
    elif KospexUtils.is_base64(request_id):
        params["author_email"] = KospexUtils.decode_base64(request_id)
    else:
        params["server"] = request_id

    return params
