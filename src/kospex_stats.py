"""
Statistical helper functions for kospex
"""
from statistics import mean, median, mode, stdev

def tenure_stats(days_values):
    """
    days_values is an array of integers representing the developers and number of days
    they have been active, typically in a repository, but could be the org or server.
    E.g. [ 1, 50, 300 ] means we have three (3) developers, with 1 day, 50 days, and 300 days of tenure.
    """
    data = {
        'max': 0,
        'mean': 0,
        'mode': 0,
        'median': 0,
        'std_dev': 0
    }

    # It's possibly we get an empty array from a specific query, depending
    # on whats in a repo, more likely for active developers, where there might be none.
    # so we'll handle that
    if days_values and len(days_values) > 0:
        data['max'] = round(max(days_values))
        data['mean'] = round(mean(days_values), 2)
        data['mode'] = round(mode(days_values), 2)
        data['median'] = round(median(days_values), 2)
        # A Standard Deviation requires at least two values
        if len(days_values) > 1:
            data['std_dev'] = round(stdev(days_values), 2)
        else:
            data['std_dev'] = 0

    return data
