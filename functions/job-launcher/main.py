import os
import pdb
import sys
import json
import time
import uuid
import yaml
import base64
import random
import hashlib

import trellisdata as trellis

from google.cloud import storage
from google.cloud import pubsub

from datetime import datetime
from dsub.commands import dsub

from envyaml import EnvYAML

ENVIRONMENT = os.environ.get('ENVIRONMENT')
if ENVIRONMENT == 'google-cloud':
    # Set up the Google Cloud Logging python client library
    # source: https://cloud.google.com/blog/products/devops-sre/google-cloud-logging-python-client-library-v3-0-0-release
    import google.cloud.logging
    client = google.cloud.logging.Client()
    # log_level=10 is equivalent to DEBUG; default is 20 == INFO
    # Gcloud Python logging client: https://googleapis.dev/python/logging/latest/client.html?highlight=setup_logging#google.cloud.logging_v2.client.Client.setup_logging
    # Logging levels: https://docs.python.org/3/library/logging.html#logging-levels
    client.setup_logging(log_level=10)

    # use Python's standard logging library to send logs to GCP
    import logging

    FUNCTION_NAME = os.environ['K_SERVICE']
    PROJECT_ID = os.environ['PROJECT_ID']
    ENABLE_JOB_LAUNCH = os.environ['ENABLE_JOB_LAUNCH']
    TOPIC_DB_QUERY = os.environ['TOPIC_DB_QUERY']

    PUBLISHER = pubsub.PublisherClient()

def launch_dsub_task(dsub_args):
    try:
        result = dsub.dsub_main('dsub', dsub_args)
    except ValueError as exception:
        logging.error(f'Problem with dsub arguments: {dsub_args}')
        raise
    except:
        print("> Unexpected error:", sys.exc_info())
        raise
    return(result)

def create_dsub_job_args(job_configuration):
    """ Convert the job description dictionary into a list
        of dsub supported arguments.

    Args:
        neo4j_job_dict (dict): Event payload.
    Returns:
        list: List of "--arg", "value" pairs which will
            be passed to dsub.
    """

    dsub_args = [
        "--name", job_configuration['name'],
        "--label", f"job-request-id={job_configuration['job_request_id']}",
        "--project", job_configuration["project"],
        "--min-cores", job_configuration['virtual_machine.min_cores'], 
        "--min-ram", job_configuration['virtual_machine.min_ram'],
        "--boot-disk-size", job_configuration['virtual_machine.boot_disk_size'],
        "--disk-size", job_configuration["virtual_machine.disk_size"],
        "--image", job_configuration["virtual_machine.image"],
        "--provider", job_configuration["dsub.provider"],
        "--regions", job_configuration["dsub.regions"],
        "--user", job_configuration["dsub.user"], 
        "--logging", job_configuration["dsub.logging"],
        "--script", job_configuration["dsub.script"],
        "--use-private-address",
        "--enable-stackdriver-monitoring",
    ]

    if job_configuration.get('network'):
        dsub_args['--network'] = job_configuration['network']
    if job_configuration.get('subnetwork'):
        dsub_args['--subnetwork'] = job_configuration['subnetwork']

    # Argument lists
    for key, value in job_configuration["dsub.inputs"].items():
        dsub_args.extend([
                          "--input", 
                          f"{key}={value}"])
    for key, value in job_configuration['dsub.environment_variables'].items():
        dsub_args.extend([
                          "--env",
                          f"{key}={value}"])
    for key, value in job_configuration['dsub.outputs'].items():
        dsub_args.extend([
                          "--output",
                          f"{key}={value}"])
    for key, value in job_configuration['dsub.labels'].items():
        dsub_args.extend([
                          "--label",
                          f"{key}={value}"])

    return dsub_args

def launch_job(event, context):
    """When an object node is added to the database, launch any
       jobs corresponding to that node label.

       Args:
            event (dict): Event payload.
            context (google.cloud.functions.Context): Metadata for the event.
    """
    
    query_response = trellis.QueryResponseReader(
                        context = context,
                        event = event)

    logging.info(f"> job-launcher: Received message (context): {query_response.context}.")
    logging.info(f"> job-launcher: Message header: {query_response.header}.")
    logging.info(f"> job-launcher: Message body: {query_response.body}.")

    # TODO: Get and validate the JobRequest node
    # Check that there is only one node
    # Check that it is a JobRequest node
    if len(query_response.nodes) > 1:
        raise ValueError("> job-launcher: Expected a single JobRequest node, but got multiple nodes.")
    
    job_request_node = query_response.nodes[0]
    if not 'JobRequest' in job_request_node['labels']:
        raise ValueError("> job-launcher: Expected node to have label 'JobRequest', but it does not: {job_request_node['labels']}.")

    # Set the JobRequest node properties to be environment variables
    # so that when the function loads the task configuration file,
    # it automatically populates the variable values with those
    # from the the JobRequest node that are now stored as environment
    # variables.
    logging.debug(f"> job-launcher: JobRequest node properties = {job_request_node['properties']}")
    for key, value in job_request_node['properties'].items():
        os.environ[key] = str(value)

    # TODO: Try using EnvYAML to load task configurations and replace
    # environment variables. Source: https://pypi.org/project/envyaml/

    # Get the name of the JobRequest
    # Using the name, get the JobLauncher configuration
    #job_configuration = TASKS[job_request_node['name']]
    job_name = job_request_node['properties']['name']
    job_configuration = EnvYAML(f'jobs/{job_name}.yaml')

    dsub_args = create_dsub_job_args(job_configuration)
    logging.debug(f"> job-launcher: Dsub arguments = {dsub_args}.")

    # Optional flags
    if ENABLE_JOB_LAUNCH == 'false':
        dsub_args.append("--dry-run")
    
    # Launch dsub job
    """Commenting this all out for development
    logging.info(f"> job-launcher: Launching dsub with args: {dsub_args}.")
    dsub_result = launch_dsub_task(dsub_args)
    logging.info(f"> job-launcher: Dsub result: {dsub_result}.")
    """
    dsub_result = {'job-id': 'test'}

    # Disable: EnvYAML does not support item assignment.
    if 'job-id' in dsub_result.keys():
        job_node = {}
        # Add dsub job ID to neo4j database node
        job_node['dsubJobId'] = dsub_result['job-id']
        job_node['dstatCmd'] = (
                                "dstat " +
                                f"--project {job_configuration['project']} " +
                                f"--provider {job_configuration['dsub.provider']} " +
                                f"--jobs '{dsub_result['job-id']}' " +
                                f"--users '{job_configuration['dsub.user']}' " +
                                 "--full " +
                                 "--format json " +
                                 "--status '*'")
        job_node['name'] = job_configuration['name']
        job_node['jobRequestId'] = job_configuration['job_request_id']
        # Virtual machine parameters
        job_node['minCores'] = int(job_configuration['virtual_machine.min_cores'])
        job_node['minRam'] = float(job_configuration['virtual_machine.min_ram'])
        job_node['bootDiskSize'] = int(job_configuration['virtual_machine.boot_disk_size'])
        job_node['diskSize'] = int(job_configuration['virtual_machine.disk_size'])
        job_node['image'] = job_configuration['virtual_machine.image']
        # Dsub parameters
        job_node['logging'] = job_configuration['dsub.logging']
        job_node['script'] = job_configuration['dsub.script']
        job_node['provider'] = job_configuration['dsub.provider']


        """I think I'm trying to hard with this. Just going to drop it for now.
        # Format inputs for neo4j database
        for key, value in job_configuration["dsub.inputs"].items():
            job_dict[f"input_{key}"] = value
        for key, value in job_dict["envs"].items():
            job_dict[f"env_{key}"] = value
        for key, value in job_dict["outputs"].items():
            job_dict[f"output_{key}"] = value
        """

        # TypeError: 'EnvYAML' object does not support item deletion
        # Remove dicts from the job_dict because Neo4j can't handle them.
        #del job_configuration["dsub.inputs"]
        #del job_configuration["dsub.outputs"]
        #del job_configuration["dsub.environment_variables"]
        #del job_configuration["dsub.labels"]

        # Create query request
        query_request = trellis.QueryRequestWriter(
            sender = FUNCTION_NAME,
            seed_id = query_response.seed_id,
            previous_event_id = query_response.event_id,
            query_name = "createDsubJobNode",
            query_parameters = job_node)
        message = query_request.format_json_message()

        logging.info(f"> job-launcher: Pubsub message: {message}.")
        result = trellis.utils.publish_to_pubsub_topic(
                    publisher = PUBLISHER,
                    project_id = PROJECT_ID,
                    topic = TOPIC_DB_QUERY,
                    message = message) 
        logging.info(f"> job-launcher: Published message to {TOPIC_DB_QUERY} with result: {result}.")
