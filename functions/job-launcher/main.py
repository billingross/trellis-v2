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

    # Load Trellis configuration
    config_doc = storage.Client() \
                .get_bucket(os.environ['CREDENTIALS_BUCKET']) \
                .get_blob(os.environ['CREDENTIALS_BLOB']) \
                .download_as_string()
    TRELLIS_CONFIG = yaml.safe_load(config_doc)

    # Load launcher configuration
    #launcher_document = storage.Client() \
    #                    .get_bucket(os.environ['CREDENTIALS_BUCKET']) \
    #                    .get_blob(TRELLIS_CONFIG["JOB_LAUNCHER_CONFIG"]) \
    #                    .download_as_string()
    with open("job-launcher.yaml", 'r') as file_handle:
        launcher_document = file_handle.read()
    task_generator = yaml.load_all(launcher_document, Loader=yaml.FullLoader)
    TASKS = {}
    for task in task_generator:
        TASKS[task.name] = task

    PUBLISHER = pubsub.PublisherClient()

"""DEPRECATED: All these functions are deprecated since I am
simplifying the process for launching tasks to just use 
a JobRequest node and YAML configuration file to get 
all job parameters.
def _get_job_values(task, start, end, params):
    supported_value_types = {
        "int": int,
        "float": float
    }
    supported_params = [
        'inputs', 
        'env_variables'
    ]
    if not params in supported_params:
        raise ValueError(f"{params} is not in supported fields: {supported_params}")
    
    task_fields = task.dsub[params]
    
    sources = {
        "start": start['properties'],
        "end": end['properties']
    }

    # Inputs must provide either a "value" field with
    # a static value or a "template" and "source" fields
    # that will be used to generate value at runtime
    # from source [start,end] values.
    # Inspiration: https://stackoverflow.com/questions/54351740/how-can-i-use-f-string-with-a-variable-not-with-a-string-literal
    job_values = {}
    for key in task_fields:
        value = task_fields[key].get('value')
        if not value:
            source = task_fields[key]['source']
            template = task_fields[key]['template']
            value_type = task_fields[key].get('value_type')
            value = template.format(**sources[source])
            
            if value_type:
                if not value_type in supported_value_types.keys():
                    raise ValueError(f"Type {value_type} not in supported types: {supported_value_types.keys()}")
                else:
                    value = supported_value_types[value_type](value)
        job_values[key] = value
    return job_values

def _get_output_values(task, bucket, start, end, job_id):
    sources = {
        "start": start['properties'],
        "end": end['properties']
    }
    task_outputs = task.dsub['outputs']

    output_values = {}
    for key in task_outputs:
        value = task_outputs[key].get('value')
        if not value:
            source = task_outputs[key]['source']
            template = task_outputs[key]['template']
            value = template.format(**sources[source])
        value = f"gs://{bucket}/{task.name}/{job_id}/output/{value}"
        output_values[key] = value
    return output_values

def _get_label_values(task, start, end):
    sources = {
        "start": start['properties'],
        "end": end['properties']
    }
    task_labels = task.dsub['labels']

    label_values = {}
    for key in task_labels:
        value = task_labels[key].get('value')
        if not value:
            source = task_labels[key]['source']
            template = task_labels[key]['template']
            value = template.format(**sources[source])
        # Lowercase values required for GCP VM labels
        label_values[key.lower()] = value.lower()
    return label_values  

def validate_relationship_inputs(query_response, task):
    
    start = query_response.relationship['start_node']
    rel = query_response.relationship['type']
    end = query_response.relationship['end_node']

    start_label = task.inputs['relationship']['start']
    rel_type = task.inputs['relationship']['type']
    end_label = task.inputs['relationship']['end']

    if not start_label in start['labels']:
        raise ValueError(f"Start node is missing required label: {start_label}.")
    if not end_label in end['labels']:
        raise ValueError(f"End node is missing required label: {end_label}.")
    if not rel_type == rel:
        raise ValueError("Relationship is not of type: {rel_type}.")
    return start, rel, end

def create_neo4j_job_dict(task, project_id, trellis_config, start_node, end_node, job_id, input_ids, trunc_nodes_hash):
    """ Create a dictionary with all the values required
        to launch a dsub job for this task. This dictionary will
        then be transformed into a dictionary with the actual
        key-value dsub arguments. We generate this intermediate 
        dictionary in a format that is amenable to adding to 
        Neo4j to create a node representing this job.


        Args:
            task (dict): Event payload.
            start_node (dict): Dictionary with node id, labels, & properties (neo4j.Graph.Node)
            end_node (dict): Dictionary with node id, labels, & properties (neo4j.Graph.Node)
            job_id (str):
            input_ids (list):
            trunc_nodes_hash (str): 8 character alphanumeric truncated hash value
        Returns:
            dictionary: Dsub job arguments
    """

    """ Get rid of all this jazz
    env_variables = _get_job_values(
                                    task = task,
                                    start = start_node,
                                    end = end_node, 
                                    params = "env_variables")
    inputs = _get_job_values(
                             task = task,
                             start = start_node,
                             end = end_node, 
                             params = "inputs")
    outputs = _get_output_values(
                                 task = task, 
                                 bucket = trellis_config['DSUB_OUTPUT_BUCKET'],
                                 start = start_node,
                                 end = end_node,
                                 job_id = job_id)
    dsub_labels = _get_label_values(
                                    task = task, 
                                    start = start_node,
                                    end = end_node)
    """

    # Use camelcase keys for this dict because it will be added to Neo4j
    # database where camelcase is the standard.
    job_dict = {
        "name": task.name,
        #"dsubName": f"{task.dsub_prefix}-{trunc_nodes_hash[0:5]}",
        #"inputHash": trunc_nodes_hash,
        #"inputIds": input_ids,
        #"trellisTaskName": f"{}"
        "trellisTaskId": job_id,
        # Standard dsub configuration
        "provider": "google-v2",
        "user": trellis_config['DSUB_USER'],
        "regions": trellis_config['DSUB_REGIONS'],
        "project": project_id,
        #"network": trellis_config['DSUB_NETWORK'],
        #"subnetwork": trellis_config['DSUB_SUBNETWORK'],
        # Task specific dsub configuration
        "minCores": task.virtual_machine["min_cores"],
        "minRam": task.virtual_machine["min_ram"],
        "bootDiskSize": task.virtual_machine["boot_disk_size"],
        "image": f"gcr.io/{project_id}/{task.virtual_machine['image']}",
        "logging": f"gs://{trellis_config['DSUB_LOG_BUCKET']}/{task.name}/{job_id}/logs",
        "diskSize": task.virtual_machine['disk_size'],
        "preemptible": task.dsub['preemptible'],
        "command": task.dsub['command'],
        # Parameterized values
        "envs": task.env_variables,
        "inputs": task.inputs,
        "outputs": outputs,
        "dsubLabels": dsub_labels
    }
    return job_dict
"""

def launch_dsub_task(dsub_args):
    try:
        result = dsub.dsub_main('dsub', dsub_args)
    except ValueError as exception:
        logging.error(f'Problem with dsub arguments: {dsub_args}')
        raise
    except:
        print("> Unexpected error:", sys.exc_info())
        raise
        #for arg in dsub_args:
        #    print(arg)
        #return(sys.exc_info())
    return(result)

def create_dsub_job_args_OLD(job_dict):
    """ Convert the job description dictionary into a list
        of dsub supported arguments.

    Args:
        neo4j_job_dict (dict): Event payload.
    Returns:
        list: List of "--arg", "value" pairs which will
            be passed to dsub.
    """

    dsub_args = [
        "--name", job_dict["dsubName"],
        "--label", f"trellis-id={job_dict['trellisTaskId']}",
        "--label", f"trellis-name={job_dict['name']}",
        "--label", f"wdl-call-alias={job_dict['name']}",
        "--provider", job_dict["provider"], 
        "--user", job_dict["user"], 
        "--regions", job_dict["regions"],
        "--project", job_dict["project"],
        "--min-cores", str(job_dict["minCores"]), 
        "--min-ram", str(job_dict["minRam"]),
        "--boot-disk-size", str(job_dict["bootDiskSize"]), 
        "--image", job_dict["image"], 
        "--logging", job_dict["logging"],
        "--disk-size", str(job_dict["diskSize"]),
        "--command", job_dict["command"],
        "--use-private-address",
        #"--network", job_dict["network"],
        #"--subnetwork", job_dict["subnetwork"],
        "--enable-stackdriver-monitoring",
    ]

    # Argument lists
    for key, value in job_dict["inputs"].items():
        dsub_args.extend([
                          "--input", 
                          f"{key}={value}"])
    for key, value in job_dict['envs'].items():
        dsub_args.extend([
                          "--env",
                          f"{key}={value}"])
    for key, value in job_dict['outputs'].items():
        dsub_args.extend([
                          "--output",
                          f"{key}={value}"])
    for key, value in job_dict['dsubLabels'].items():
        dsub_args.extend([
                          "--label",
                          f"{key}={value}"])

    return dsub_args

def create_dsub_job_args(task_configuration):
    """ Convert the job description dictionary into a list
        of dsub supported arguments.

    Args:
        neo4j_job_dict (dict): Event payload.
    Returns:
        list: List of "--arg", "value" pairs which will
            be passed to dsub.
    """

    dsub_args = [
        "--name", task_configuration['name'],
        "--label", f"job-request-id={task_configuration['job_request_id']}",

        "--project", task_configuration["project"],
        "--min-cores", task_configuration['virtual_machine.minCores'], 
        "--min-ram", task_configuration['virtual_machine.minRam'],
        "--boot-disk-size", task_configuration['virtual_machine.bootDiskSize'],
        "--disk-size", task_configuration["virtual_machine.diskSize"],
        "--image", task_configuration["virtual_machine.image"],
        "--provider", task_configuration["dsub.provider"],
        "--regions", task_configuration["dsub.regions"],
        "--user", task_configuration["dsub.user"], 
        "--logging", task_configuration["dsub.logging"],
        "--script", task_configuration["dsub.script"],
        "--use-private-address",
        "--enable-stackdriver-monitoring",
    ]

    if task_configuration.get('network'):
        dsub_args['--network'] = task_configuration['network']
    if task_configuration.get('subnetwork'):
        dsub_args['--subnetwork'] = task_configuration['subnetwork']

    # Argument lists
    for key, value in task_configuration["dsub.inputs"].items():
        dsub_args.extend([
                          "--input", 
                          f"{key}={value}"])
    for key, value in job_dict['dsub.environment_variables'].items():
        dsub_args.extend([
                          "--env",
                          f"{key}={value}"])
    for key, value in job_dict['dsub.outputs'].items():
        dsub_args.extend([
                          "--output",
                          f"{key}={value}"])
    for key, value in job_dict['dsub.labels'].items():
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
    if len(query_response.nodes) > 1
        raise ValueError("Expected a single JobRequest node, but got multiple nodes.")
    
    job_request_node = query_response.nodes[0]
    if not 'JobRequest' in job_request_node['labels']:
        raise ValueError("Expected node to have label 'JobRequest', but it does not: {job_request_node['labels']}.")

    # Set the JobRequest node properties to be environment variables
    # so that when the function loads the task configuration file,
    # it automatically populates the variable values with those
    # from the the JobRequest node that are now stored as environment
    # variables.
    for key, value in job_request_node['properties'].items():
        os.environ[key] = value

    # TODO: Try using EnvYAML to load task configurations and replace
    # environment variables. Source: https://pypi.org/project/envyaml/

    # Get the name of the JobRequest
    # Using the name, get the JobLauncher configuration
    #task_configuration = TASKS[job_request_node['name']]
    task_name = job_request_node['properties']['name']
    task_configuration = EnvYAML(f'tasks/{task_name}.yaml')

    # Why do I need this? Why can't I just use the task_configuration
    # variable directly?
    """I'm pretty sure I can.
    job_dict = create_neo4j_job_dict(
                               task = task,
                               project_id = PROJECT_ID,
                               trellis_config = TRELLIS_CONFIG,
                               start_node = start,
                               end_node = end,
                               job_id = job_id,
                               input_ids = input_ids,
                               trunc_nodes_hash = trunc_nodes_hash)
    logging.debug(f"> job-launcher: Job dictionary = {job_dict}.")
    """

    #dsub_args = create_dsub_job_args(job_dict)
    dsub_args = create_dsub_job_args(task_configuration)
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

    if 'job-id' in dsub_result.keys():
        # Add dsub job ID to neo4j database node
        task_configuration['dsubJobId'] = dsub_result['job-id']
        task_configuration['dstatCmd'] = (
                                 "dstat " +
                                f"--project {job_dict['project']} " +
                                f"--provider {job_dict['provider']} " +
                                f"--jobs '{job_dict['dsubJobId']}' " +
                                f"--users '{job_dict['user']}' " +
                                 "--full " +
                                 "--format json " +
                                 "--status '*'")

        """I think I'm trying to hard with this. Just going to drop it for now.
        # Format inputs for neo4j database
        for key, value in task_configuration["dsub.inputs"].items():
            job_dict[f"input_{key}"] = value
        for key, value in job_dict["envs"].items():
            job_dict[f"env_{key}"] = value
        for key, value in job_dict["outputs"].items():
            job_dict[f"output_{key}"] = value
        """

        # Remove dicts from the job_dict because Neo4j can't handle them.
        del task_configuration["dsub.inputs"]
        del task_configuration["dsub.outputs"]
        del task_configuration["dsub.environment_variables"]
        del task_configuration["dsub.labels"]

        # Create query request
        query_request = trellis.QueryRequestWriter(
            sender = FUNCTION_NAME,
            seed_id = query_response.seed_id,
            previous_event_id = query_response.event_id,
            query_name = "createDsubJobNode",
            query_parameters = task_configuration)
        message = query_request.format_json_message()

        logging.info(f"> job-launcher: Pubsub message: {message}.")
        result = trellis.utils.publish_to_pubsub_topic(
                    publisher = PUBLISHER,
                    project_id = PROJECT_ID,
                    topic = TRELLIS_CONFIG['TOPIC_DB_QUERY'],
                    message = message) 
        logging.info(f"> job-launcher: Published message to {TRELLIS_CONFIG['TOPIC_DB_QUERY']} with result: {result}.")
