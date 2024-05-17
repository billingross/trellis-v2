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
    TOPIC_DB_QUERY = os.environ['TOPIC_DB_QUERY']

    PUBLISHER = pubsub.PublisherClient()
    # Storage client is used for adding UUIDs to blobs
    STORAGE_CLIENT = storage.Client()

    triggers_file = '1000-genomes-triggers.yaml'
    with open(triggers_file, 'r') as file_handle:
        OBJECT_TRIGGERS = yaml.safe_load(file_handle)

class OldNodeKinds:

    def __init__(self):
        """Use to determine which kind of database node should be created.
        """

        self.match_patterns = {
            "Blob": [r"^(?P<plate>\w+)/(?P<sample>\w+)/(?P<trellis_task>\w+(?:-*\w*)+)/(?P<trellis_task_id>\w+(?:-\w+)+)/.*"],
            "Vcf": [
                    ".*\\.vcf.gz$", 
                    ".*\\.vcf$",
            ],
            "Gvcf": [
                    ".*\\.g.vcf.gz$",
                    ".*\\.g.vcf$",
            ],
            "Cram": [".*\\.cram$"], 
            "Bam": [".*\\.bam$"], 
            "Ubam": [".*\\.ubam$"],

            "Log": [
                    ".*\\.log$",
                    ".*\\/stderr$",
                    ".*\\/stdout$"],
            "Index": [
                      ".*\\.bai$",
                      ".*\\.tbi$",
                      ".*\\.crai$",
            ],
            "Json": [".*\\.json$"],
            "Fastqc": [".*/bam-fastqc/.*"],
            "Flagstat": [".*/flagstat/.*"],
            "Vcfstats": [".*/vcfstats/.*"],
            "TextToTable": [".*/text-to-table/.*"],
            "CheckContamination": [".*/call-CheckContamination/.*"],
        }

        self.label_functions = {
                                "Blob": [
                                         trellis_metadata_groupdict,
                                ],
                                "Shard": [
                                          shard_index_name_1,
                                ],
                                "Cromwell": [
                                         cromwell_metadata_groupdict,
                                ],
                                "Ubam": [
                                         read_group_name_1,
                                ],
        }

def match_database_queries(event_name, object_metadata, object_triggers):
    # DEPRECATED: define trigger patterns in separate YAML file
    # Define query patterns
    #query_patterns = {}
    #query_patterns['relatePlateToSample'] = r"/(?P<plate>\w+)/HAS_SAMPLE/(?P<sample>[a-zA-Z0-9]+)/"
    #query_patterns['relateSampleToReadGroup'] = r"/(?P<sample>\w+)/HAS_READ_GROUP/(?P<read_group>\w+)/"
    #query_patterns['relateReadGroupToFastq'] = r"/(?P<read_group>\w+)/HAS_FASTQ/(?P<sample>[a-zA-Z0-9]+)_(?P<mate_pair>[1-2])\.fastq.gz$"
    #query_patterns['mergeFastq'] = r"(?P<sample>[a-zA-Z0-9]+)_(?P<mate_pair>[1-2])\.fastq.gz$"

    queries_to_request = {}
    for query, regex_pattern in object_triggers.items():
        match = re.search(regex_pattern, event_name)
        if match:
            query_parameters = match.groupdict()
            query_parameters.update(object_metadata)
            queries_to_request[query] = query_parameters
    return queries_to_request

def check_object_triggers(event, context, test=False):
    """When object created in bucket, add metadata to database.
    Args:
        event (dict): Event payload.
        context (google.cloud.functions.Context): Metadata for the event.
    """
    logging.info(f"> check-object-triggers: Processing new object event: {event['name']}.")
    logging.info(f"> check-object-triggers: Event: {event}.")
    logging.info(f"> check-object-triggers: Context: {context}.")
    logging.debug(f"> check-object-triggers: Environment: {ENVIRONMENT}.")
    
    seed_id = context.event_id

    logging.info(f"> check-object-triggers: Matching database query patterns.")
    queries_to_request = match_database_queries(event['name'], event['metadata'])

    logging.info(f"> check-object-triggers: Object matched {len(queries_to_request)} query patterns.")
    for query_name, parameters in queries_to_request.items():
        # Create query request
        query_request = trellis.QueryRequestWriter(
            sender = FUNCTION_NAME,
            seed_id = seed_id,
            previous_event_id = seed_id,
            query_name = query_name,
            query_parameters = parameters)

        message = query_request.format_json_message()
        logging.info(f"> check-object-triggers: Publishing message '{message}' to topic '{TOPIC_DB_QUERY}'.")
        if ENVIRONMENT == 'google-cloud':
            result = trellis.utils.publish_to_pubsub_topic(
                publisher = PUBLISHER,
                project_id = PROJECT_ID,
                topic = TOPIC_DB_QUERY,
                message = message)
            logging.info(f"> check-object-triggers: Published message to {TOPIC_DB_QUERY} with result: {result}.")
        else:
            logging.warning("> check-object-triggers: Could not determine environment. Message was not published.")
            return(message)