import os
import re
import pdb
import json
import uuid
import yaml
import iso8601
import importlib

import trellisdata as trellis

from collections import deque

from google.cloud import storage
from google.cloud import pubsub

ENVIRONMENT = os.environ.get('ENVIRONMENT', 'Environment variable not set')
if ENVIRONMENT == 'google-cloud':

    # set up the Google Cloud Logging python client library
    # source: https://cloud.google.com/blog/products/devops-sre/google-cloud-logging-python-client-library-v3-0-0-release
    import google.cloud.logging
    client = google.cloud.logging.Client()
    client.setup_logging()

    # use Python's standard logging library to send logs to GCP
    import logging

    FUNCTION_NAME = os.environ['K_SERVICE']
    PROJECT_ID = os.environ['PROJECT_ID']

    PUBLISHER = pubsub.PublisherClient()
    # Storage client is used for adding UUIDs to blobs
    STORAGE_CLIENT = storage.Client()

def get_name_fields(event_name, event_bucket):
    """

    Example input:
        event_name: va_mvp_phase2/PLATE0/SAMPLE0/FASTQ/SAMPLE0_0_R1.fastq.gz
        event_bucket: gcp-bucket-mvp-test-from-personalis
    
    Example output:
        path: va_mvp_phase2/PLATE0/SAMPLE0/FASTQ/SAMPLE0_0_R1.fastq.gz
        dirname: va_mvp_phase2/PLATE0/SAMPLE0/FASTQ
        basename: SAMPLE0_0_R1.fastq.gz
        name: SAMPLE0_0_R1
        extension: fastq.gz
        filetype: gz
        uri: gs://gcp-bucket-mvp-test-from-personalis/va_mvp_phase2/PLATE0/SAMPLE0/FASTQ/SAMPLE0_0_R1.fastq.gz
    """
    path_elements = event_name.split('/')
    name_elements = path_elements[-1].split('.')
    name_fields = {
                   "path": event_name,
                   "dirname": '/'.join(path_elements[:-1]),
                   "basename": path_elements[-1],
                   "name": name_elements[0],
                   "extension": '.'.join(name_elements[1:]),
                   "filetype": name_elements[-1],
                   "uri" : "gs://" + event_bucket + "/" + event_name,
    }
    return name_fields

def get_time_fields(event):
    """
    Args:
        event (dict): Metadata properties stored as strings
    Return
        (dict): Times in iso (str) and from-epoch (int) formats
    """

    # Google datetime format: https://tools.ietf.org/html/rfc3339
    # ISO 8601 standard format: https://en.wikipedia.org/wiki/ISO_8601
    datetime_created = iso8601.parse_date(event['timeCreated'])
    datetime_updated = iso8601.parse_date(event['updated'])


    time_created_epoch = trellis.utils.get_seconds_from_epoch(datetime_created)
    time_created_iso = datetime_created.isoformat()

    time_updated_epoch = trellis.utils.get_seconds_from_epoch(datetime_updated)
    time_updated_iso = datetime_updated.isoformat()

    time_fields = {
                   'timeCreatedEpoch': time_created_epoch,
                   'timeUpdatedEpoch': time_updated_epoch,
                   'timeCreatedIso': time_created_iso,
                   'timeUpdatedIso': time_updated_iso
    }
    return time_fields

def add_uuid_to_blob(bucket, path):
    """For a json object, get and return json data.

    Args:
        bucket (str): Name of Google Cloud Storage (GCS) bucket.
        path (str): Path to GCS object.
    Returns:
        Blob.metadata (dict)
    """
    metadata = {'trellis-uuid': uuid.uuid4()}

    storage_client = storage.Client()
    bucket = STORAGE_CLIENT.get_bucket(bucket)
    blob = bucket.get_blob(path)
    blob.metadata = metadata
    blob.patch()

    return blob.metadata

def assign_labels_and_metadata(query_parameters, label_patterns, label_functions):
    #query_parameters['labels'] = []
    labels = []
    for label, patterns in label_patterns.items():
        for pattern in patterns:
            match = re.fullmatch(pattern, query_parameters['path'])
            if match:
                labels.append(label)
                #query_parameters['labels'].append(label)
                metadata_functions = label_functions.get(label)
                if metadata_functions:
                    for metadata_function in metadata_functions:
                        custom_fields = metadata_function(query_parameters, match.groupdict())
                        query_parameters.update(custom_fields)
                # Break after a single pattern per label has been matched
                # According to single-label mode, objects can't/shouldn't(?)
                # match more than one label.
                break
    return query_parameters, labels


def add_object_metadata(event, context, test=False):
    """When object created in bucket, add metadata to database.
    Args:
        event (dict): Event payload.
        context (google.cloud.functions.Context): Metadata for the event.
    """
    logging.info(f"> check-object-triggers: Processing new object event: {event['name']}.")
    logging.info(f"> check-object-triggers: Event: {event}.")
    logging.info(f"> check-object-triggers: Context: {context}.")

    seed_id = context.event_id

    # Trellis config data
    name = event['name']
    bucket_name = event['bucket']

    logging.debug(f"> check-object-triggers: Environment: {ENVIRONMENT}.")

    node_kinds = node_module.NodeKinds()
    label_patterns = node_kinds.match_patterns
    label_functions = node_kinds.label_functions
    logging.debug(f"> check-object-triggers: Label patterns: {len(label_patterns)}, label functions: {len(label_functions)}.")
    
    # Add UUID metadata field to object in Google Cloud Storage.
    # Check whether UUID has been generated for blob. If not, create one.
    event_metadata = event.get('metadata')
    if event_metadata:
        uuid = event_metadata.get('trellis-uuid')
        if not uuid:
            uuid = add_uuid_to_blob(
                                    event['bucket'], 
                                    event['name'])
            logging.debug(f"> check-object-triggers: Object UUID added: {uuid}. Exiting.")
            return # Updating metadata will trigger this function again
    else:
        uuid = add_uuid_to_blob(
                        event['bucket'], 
                        event['name'])
        logging.debug(f"> check-object-triggers: Object UUID added: {uuid}. Exiting.")
        return # Updating metadata will trigger this function again

    # Default logic is to add ALL metadata to object node. May revisit this.
    #query_parameters = clean_metadata_dict(event)
    #logging.debug(f"> check-object-triggers: Cleaned object metadata: {query_parameters}.")

    # Add standard fields
    name_fields = get_name_fields(
                    event_name = event['name'], 
                    event_bucket = event['bucket'])
    time_fields = get_time_fields(event)

    query_parameters.update(name_fields)
    query_parameters.update(time_fields)
    logging.info(f"> check-object-triggers: Query parameters: {query_parameters}.")

    # This seems like it should just be used for debugging
    # Add trigger operation as metadata property
    query_parameters['triggerOperation'] = TRIGGER_OPERATION

    # >>> This is the meat of the function.
    # Populate query_parameters with metadata about object
    logging.debug(f"> check-object-triggers: Query parameter 'path': {query_parameters['path']}.")
    query_parameters, labels = assign_labels_and_metadata(query_parameters, label_patterns, label_functions)
    logging.debug(f"> check-object-triggers: Labels assigned to node: {labels}.")

    labels = get_leaf_labels(labels, TAXONOMY_PARSER)
    logging.info(f"> check-object-triggers: Leaf labels (expect one): {labels}.")

    if 'Log' in labels:
        logging.info(f"> check-object-triggers: This is a log file; ignoring.")
        return

    # Max (1) label per node to choose parameterized query
    if len(labels) > 1:
        logging.error(f"> check-object-triggers: More than one label applied to node: [{labels}].")
    elif not labels:
        logging.error("> check-object-triggers: No labels applied to node.")
    else:
        label = labels[0]

    # Generate UUID
    if not query_parameters.get('trellisUuid') and ENVIRONMENT == 'google-cloud':
        uuid = add_uuid_to_blob(
                                query_parameters['bucket'], 
                                query_parameters['path'])
        logging.debug("> check-object-triggers: The metadata for the blob {} is {}".format(blob.name, blob.metadata))
        query_parameters['trellisUuid'] = blob.metadata['uuid']


    # Dynamically create parameterized query
    parameterized_query = create_parameterized_merge_query(label, query_parameters)

    query_request = trellis.QueryRequestWriter(
        sender = FUNCTION_NAME,
        seed_id = seed_id,
        previous_event_id = seed_id,
        query_name = f"mergeBlob{label}",
        query_parameters = query_parameters,
        custom = True,
        cypher = parameterized_query,
        write_transaction = True,
        aggregate_results = False,
        publish_to = ["TOPIC_TRIGGERS"],
        returns = {
                   "pattern": "node",
                   "start": label
        })
    message = query_request.format_json_message()
    logging.info(f"> check-object-triggers: Topic: {TRELLIS['TOPIC_DB_QUERY']}, message: {message}.")
    if ENVIRONMENT == 'google-cloud':
        result = trellis.utils.publish_to_pubsub_topic(
            publisher = PUBLISHER,
            project_id = PROJECT_ID,
            topic = TRELLIS['TOPIC_DB_QUERY'],
            message = message)
        logging.info(f"> check-object-triggers: Published message to {TRELLIS['TOPIC_DB_QUERY']} with result: {result}.")
    else:
        logging.warning("> check-object-triggers: Could not determine environment. Message was not published.")
        return(message)