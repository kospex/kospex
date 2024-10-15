""" Helper functions for kospex web UI """
import csv
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

def get_id_params(id):
    """
    Parse an id into either:
        repo_id
        org_key
        server
    and return a dict with that key to be passed into a KospexQuery
    """
    params = {}

    if id is None:
        return params

    if org_key := KospexUtils.parse_org_key(id):
        params["org_key"] = id
    elif repo_id := KospexUtils.parse_repo_id(id):
        params["repo_id"] = id
    else:
        params["server"] = id

    return params
