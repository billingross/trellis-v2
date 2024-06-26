import re
import json
import pytz
import uuid
import iso8601

from datetime import datetime

from google.cloud import storage

def clean_metadata_dict(raw_dict):
    """Remove dict entries where the value is of type dict"""
    clean_dict = dict(raw_dict)

    # Remove values that are dicts
    delete_keys = []
    for key, value in clean_dict.items():
        if isinstance(value, dict):
            #del clean_dict[key]
            delete_keys.append(key)

    for key in delete_keys:
        del clean_dict[key]

    # Convert size field from str to int
    clean_dict['size'] = int(clean_dict['size'])

    return clean_dict


def _get_seconds_from_epoch(datetime_obj):
    """Get datetime as total seconds from epoch.

    Provides datetime in easily sortable format

    Args:
        datetime_obj (datetime): Datetime.
    Returns:
        (float): Seconds from epoch
    """
    from_epoch = datetime_obj - datetime(1970, 1, 1, tzinfo=pytz.UTC)
    from_epoch_seconds = from_epoch.total_seconds()
    return from_epoch_seconds


def _search_string(name, string, pattern, group, req_type):
    # kwargs: [pattern, string, group, req_type]
    """Calls regex search function using specified values. 

    Args:
        pattern (str): Pattern to search for. 
        string (str): String that will be searched. 
        group (int): Index of matching group to be returned.
        req_type (type): Type of value that should be returned.

    Returns:
        value (req_type): (n)th element of group elements, where 
                          n==group and type==req_type.

    """
    match = re.search(pattern, string)
    if not match:
        # Throw exception
        #print("Error: no match found")
        raise ValueError(f"Could not find '{name}'' in string '{string}'' following expression pattern '{pattern}'.")
    else:
        match_value = match.group(group)

    typed_value = req_type(match_value)

    return(typed_value)


def get_datetime_iso8601(date_string):
    """ Convert ISO 86801 date strings to datetime objects.

    Google datetime format: https://tools.ietf.org/html/rfc3339
    ISO 8601 standard format: https://en.wikipedia.org/wiki/ISO_8601
    
    Args:
        date_string (str): Date in ISO 8601 format
    Returns
        (datetime.datetime): Datetime objects
    """
    return iso8601.parse_date(date_string)


def get_standard_name_fields(event_name):
    path_elements = event_name.split('/')
    name_elements = path_elements[-1].split('.')
    name_fields = {
                   "path": event_name,
                   "dirname": '/'.join(path_elements[:-1]),
                   "basename": path_elements[-1],
                   "name": name_elements[0],
                   "extension": '.'.join(name_elements[1:])
    }
    return name_fields


def get_standard_time_fields(event):
    """
    Args:
        event (dict): Metadata properties stored as strings
    Return
        (dict): Times in iso (str) and from-epoch (int) formats
    """
    datetime_created = get_datetime_iso8601(event['timeCreated'])
    datetime_updated = get_datetime_iso8601(event['updated'])


    time_created_epoch = _get_seconds_from_epoch(datetime_created)
    time_created_iso = datetime_created.isoformat()

    time_updated_epoch = _get_seconds_from_epoch(datetime_updated)
    time_updated_iso = datetime_updated.isoformat()

    time_fields = {
                   'timeCreatedEpoch': time_created_epoch,
                   'timeUpdatedEpoch': time_updated_epoch,
                   'timeCreatedIso': time_created_iso,
                   'timeUpdatedIso': time_updated_iso
    }
    return time_fields

## Functions for paring custom metadata from blob metadata

def trellis_metadata_groupdict(db_dict, groupdict):
    return {
            'plate': groupdict['plate'],
            'sample': groupdict['sample'],
    }


def mate_pair_name_0(db_dict, groupdict):
    """Look for mate pair value at the end of the Fastq name.
    Example: fastq_0_R1.fastq.gz
    """
    mate_pair = _search_string(
                          name = "mate_pair_name_0",
                          string = db_dict['name'], 
                          pattern = "_R(\\d)$", 
                          group = 1, 
                          req_type = int)
    return {'matePair': mate_pair}


def read_group_name_1(db_dict, groupdict):
    """Get 2nd element of db_dict['name'] property & return as readGroup.
    Example: fastq_0_R1.fastq.gz
    """
    index = db_dict['name'].split('_')[1]
    return {'readGroup': int(index)}  


def read_json(db_dict, groupdict):
    """For a json object, get and return json data.

    Args:
        db_dict(dict): Properties from GCS & standard metadata fields
            generated by Trellis.
        groupdict(dict): Properties generated by match() operation
            to determine whether object path matches a node pattern.
    """
    json_content = storage.Client() \
        .get_bucket(db_dict['bucket']) \
        .blob(db_dict['path']) \
        .download_as_string()
    json_data = json.loads(json_content)
    return json_data


def read_checksum(db_dict, groupdict):
    """Parse data from a checksum.txt object from Personalis.

    Args:
        db_dict(dict): Properties from GCS & standard metadata fields
            generated by Trellis.
        groupdict(dict): Properties generated by match() operation
            to determine whether object path matches a node pattern.
    """
    data = storage.Client() \
        .get_bucket(db_dict['bucket']) \
        .blob(db_dict['path']) \
        .download_as_string()
    decoded_data = data.decode("utf-8")
    # Strip trailing newline
    stripped_data = decoded_data.rstrip()
    split_data = stripped_data.split('\n')

    # Count the number of fastqs & microarray data
    fastq_pattern = r"(?P<checksum>\w+)\t+./FASTQ/(?P<basename>.*\.fastq\.gz)"
    microarray_pattern = r"(?P<checksum>\w+)\t+./Microarray/(?P<basename>.*)"
    
    fastq_counter = 0
    microarray_counter = 0

    json_data = {}
    for line in split_data:
        fastq_match = re.fullmatch(fastq_pattern, line)
        if fastq_match:
            # Increment counter value for 'fastqCount' field
            fastq_counter +=1
            # Add basename/checksum as individual field (ERROR: INVALID KEY NAME)
            #json_data[fastq_match.group('basename')] = fastq_match.group('checksum')
            #checksums.append({fastq_match.group('basename'): fastq_match.group('checksum')})

        microarray_match = re.fullmatch(microarray_pattern, line)
        if microarray_match:
            microarray_counter +=1
            # Add basename/checksum as individual field (ERROR: INVALID KEY NAME)
            #json_data[microarray_match.group('basename')] = microarray_match.group('checksum')
            #checksums.append({microarray_match.group('basename'): microarray_match.group('checksum')})

    json_data['fastqCount'] = fastq_counter
    json_data['microarrayCount'] = microarray_counter
    #json_data['data'] = stripped_data
    print(f"JSON data: {json_data}")

    return json_data

# Relationship functions
def relate_job_to_output(db_dict):

    query = (
             f"MATCH (j:Job {{ taskId:\"{db_dict['taskId']}\" }} ), " +
             f"(b:Blob {{taskId:\"{db_dict['taskId']}\", " +
                       f"id:\"{db_dict['id']}\" }})" +
             f"CREATE (j)-[:OUTPUT]->(b) " +
              "RETURN b")
    return query

class NodeEntry:

    def __init__(self, event, context, labels, label_functions=[]):
        """
        Args:
            event (dict): Blob metadata generated by GCP REST API
            context (dict): Event context generated by GCP REST API
            labels (list): List of database node labels
            label_functions (list): List of functions used to get custom metadata
        
        Returns:

        """
        db_dict = clean_metadata_dict(event)

        name_fields = get_standard_name_fields(event['name'])
        time_fields = get_standard_time_fields(event)

        db_dict.update(name_fields)
        db_dict.update(time_fields)

        # This custom metadata field gets added to all nodes
        db_dict['labels'] = labels

        print(f'>> Label functions: {label_functions}.')
        for function in label_functions:
            custom_fields = function(db_dict)
            db_dict.update(custom_fields)

        self.db_dict = db_dict
        self.gcp_metadata = event

        # Key, value pairs unique to db_dict are trellis metadata
        self.trellis_metadata = {}
        for key, value in self.db_dict.items():
            if not key in self.gcp_metadata.keys():
                self.trellis_metadata[key] = value

    def get_db_dict(self):
        return(self.db_dict)

    def get_gcp_metadata(self):
        return(self.gcp_metadata)

    def get_trellis_metadata(self):
        return(self.trellis_metadata)

class NodeKinds:

    def __init__(self):
        """Use to determine which kind of database node should be created.
        """

        self.match_patterns = {
                               "Blob": [r"^va_mvp_phase2\/(?P<plate>\w+)\/(?P<sample>\w+)\/.*"],
                               "Fastq": [r"^va_mvp_phase2\/(?P<plate>\w+)\/(?P<sample>\w+)\/FASTQ\/.*\.fastq\.gz$"], 
                               "Microarray": [r"^va_mvp_phase2\/(?P<plate>\w+)\/(?P<sample>\w+)\/Microarray\/.*"], 
                               "PersonalisSequencing": [r"^va_mvp_phase2\/(?P<plate>\w+)\/(?P<sample>\w+)\/.*\.json$"],
                               #"Json": ["^va_mvp_phase2/.*\\.json$"],
                               "Checksum": [r"^va_mvp_phase2\/(?P<plate>\w+)\/(?P<sample>\w+)\/.*checksum\.txt$"], 
                               #"WGS35": ["^va_mvp_phase2/.*"],
                               #"FromPersonalis": ["^va_mvp_phase2/.*"],
        }

        self.label_functions = {
                                #"Blob": [trellis_metadata_groupdict],
                                "Fastq": [
                                          trellis_metadata_groupdict,
                                          mate_pair_name_0, 
                                          read_group_name_1],
                                "Microarray": [trellis_metadata_groupdict],
                                "PersonalisSequencing": [
                                                         trellis_metadata_groupdict,
                                                         read_json],
                                "Checksum": [
                                             trellis_metadata_groupdict,
                                             read_checksum],
        }

    def get_label_functions(self, labels):
        all_functions = []

        for label in labels:
            label_functions = self.label_functions.get(label)
            if label_functions:
                all_functions.extend(label_functions)
        return all_functions

    def get_global_labels(self):
        return self.global_labels

    def get_match_patterns(self):
        return self.match_patterns

    def get_class(self, name):
        """Return class whose name matches input string.
        """
        return self.kind_classes[name]

class RelationshipKinds:

    def __init__(self):

        self.shipping_properties = {}
