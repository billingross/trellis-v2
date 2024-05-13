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
                   'bucket': event_bucket,
                   'path': event_name,
                   'dirname': '/'.join(path_elements[:-1]),
                   'basename': path_elements[-1],
                   'name': name_elements[0],
                   'extension': '.'.join(name_elements[1:]),
                   'filetype': name_elements[-1],
                   'uri' : 'gs://' + event_bucket + '/' + event_name,
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

def add_metadata_to_object(metadata_fields):
    """For a json object, get and return json data.

    Args:
        bucket (str): Name of Google Cloud Storage (GCS) bucket.
        path (str): Path to GCS object.
        metadata (dict): Object metadat parsed by this function.
    Returns:
        Blob.metadata (dict)
    """
    bucket = STORAGE_CLIENT.get_bucket(metadata_fields['bucket'])
    blob = bucket.get_blob(metadata_fields['path'])
    blob.metadata = metadata_fields
    blob.patch()

    return blob.metadata

def parse_object_metadata(event, context, test=False):
    """When object created in bucket, add metadata to database.
    Args:
        event (dict): Event payload.
        context (google.cloud.functions.Context): Metadata for the event.
    """
    logging.info(f"> parse-object-metadata: Processing new object event: {event['name']}.")
    logging.info(f"> parse-object-metadata: Event: {event}.")
    logging.info(f"> parse-object-metadata: Context: {context}.")

    seed_id = context.event_id

    logging.debug(f"> parse-object-metadata: Environment: {ENVIRONMENT}.")

    metadata_fields = {}
    # Parse name metadata fields
    name_fields = get_name_fields(
                                  event_name = event['name'], 
                                  event_bucket = event['bucket'])
    metadata_fields.update(name_fields)

    # Parse time metadata fields
    time_fields = get_time_fields(event)
    metadata_fields.update(time_fields)

    # Add UUID to object metadata
    metadata_fields['trellisUuid'] = uuuid.uuid4()

    # Add Google Cloud Storage event metadata
    metadata_fields['size'] = event['size']
    metadata_fields['crc32c'] = event['crc32c']
    metadata_fields['gcsId'] = event['id']

    # Add metadata to object
    object_metadata = add_metadata_to_object(metadata_fields)
    logging.info(f"> parse-object-metadata: Object metadata = {object_metadata}.")

