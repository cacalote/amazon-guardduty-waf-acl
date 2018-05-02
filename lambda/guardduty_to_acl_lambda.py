# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

import boto3
import math
import time
import json
import datetime
import logging
import os
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

#======================================================================================================================
# Variables
#======================================================================================================================

ACLMETATABLE = os.environ['ACLMETATABLE']

#======================================================================================================================
# Auxiliary Functions
#======================================================================================================================
def get_netacl_id(subnet_id):
    try:
        ec2 = boto3.client('ec2')
        response = ec2.describe_network_acls(
            Filters=[
                {
                    'Name': 'association.subnet-id',
                    'Values': [
                        subnet_id,
                    ]
                }
            ]
        )

        netacls = response['NetworkAcls'][0]['Associations']

        for i in netacls:
            if i['SubnetId'] == subnet_id:
                netaclid = i['NetworkAclId']

        return netaclid
    except Exception as e:
        return []


def get_nacl_meta(netacl_id):
    ddb = boto3.resource('dynamodb')
    table = ddb.Table(ACLMETATABLE)
    ec2 = boto3.client('ec2')
    response = ec2.describe_network_acls(
        NetworkAclIds=[
            netacl_id,
            ]
    )

    # Get entries in DynamoDB table
    ddbresponse = table.scan()
    ddbentries = response['Items']

    netacl = ddbresponse['NetworkAcls'][0]['Entries']
    naclentries = []

    for i in netacl:
            entries.append(i)

    return naclentries


def update_nacl(netacl_id, host_ip):

    ddb = boto3.resource('dynamodb')
    table = ddb.Table(ACLMETATABLE)
    timestamp = int(time.time())

    hostipexists = table.query(
        KeyConditionExpression=Key('NetACLId').eq(netacl_id),
        FilterExpression=Attr('HostIp').eq(host_ip)
    )

    # Get oldest entry in DynamoDB table
    oldestrule = table.query(
        KeyConditionExpression=Key('NetACLId').eq(netacl_id),
        ScanIndexForward=True, # true = ascending, false = descending
        Limit=1,
    )

    # Is HostIp already in table?
    if hostipexists['Items']:
        logger.info("log -- host IP %s already in table... exiting." % (host_ip))

    else:

        # Get current NACL entries in DDB
        response = table.query(
            KeyConditionExpression=Key('NetACLId').eq(netacl_id)
        )

        # Get all the entries for NACL
        naclentries = response['Items']

        # Find oldest rule and current counter
        if naclentries:
            oldruleno = int((oldestrule)['Items'][0]['RuleNo'])
            oldrulets = int((oldestrule)['Items'][0]['CreatedAt'])
            rulecounter = max(naclentries, key=lambda x:x['RuleNo'])['RuleNo']
            rulecount = response['Count']

            # Set the rule number
            if int(rulecounter) < 80:
                newruleno = int(rulecounter) + 1

                # Create NACL rule and DDB state entry
                create_netacl_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)
                create_ddb_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)

                logger.info("log -- add new rule %s, HostIP %s, to NACL %s." % (newruleno, host_ip, netacl_id))
                logger.info("log -- rule count for NACL %s is %s." % (netacl_id, int(rulecount) + 1))

            else:
                newruleno = oldruleno

                # Delete old NACL rule and DDB state entry
                delete_netacl_rule(netacl_id=netacl_id, rule_no=oldruleno)
                delete_ddb_rule(netacl_id=netacl_id, created_at=oldrulets)

                logger.info("log -- delete rule %s, from NACL %s." % (oldruleno, netacl_id))

                # Create NACL rule and DDB state entry
                create_netacl_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)
                create_ddb_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)

                logger.info("log -- add new rule %s, HostIP %s, to NACL %s." % (newruleno, host_ip, netacl_id))
                logger.info("log -- rule count for NACL %s is %s." % (netacl_id, rulecount))

        else:
            # No entries in DDB Table start from 71
            newruleno = 71
            oldruleno = []
            rulecount = 0

            # Create NACL rule and DDB state entry
            create_netacl_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)
            create_ddb_rule(netacl_id=netacl_id, host_ip=host_ip, rule_no=newruleno)

            logger.info("log -- add new rule %s, HostIP %s, to NACL %s." % (newruleno, host_ip, netacl_id))
            logger.info("log -- rule count for NACL %s is %s." % (netacl_id, int(rulecount) + 1))

        if rulecount > 10:
            delete_netacl_rule(netacl_id=netacl_id, rule_no=oldruleno)

            logger.info("log -- delete rule %s, from NACL %s." % (oldruleno, netacl_id))
            logger.info("log -- rule count for NACL %s is %s." % (netacl_id, int(rulecount) + 1))

        if response['ResponseMetadata']['HTTPStatusCode'] == 200:
            return True
        else:
            return False


def create_netacl_rule(netacl_id, host_ip, rule_no):

    ec2 = boto3.resource('ec2')
    network_acl = ec2.NetworkAcl(netacl_id)

    response = network_acl.create_entry(
    CidrBlock = host_ip + '/32',
    Egress=False,
    PortRange={
        'From': 0,
        'To': 65535
    },
    Protocol='-1',
    RuleAction='deny',
    RuleNumber= rule_no
    )

    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        return True
    else:
        return False


def delete_netacl_rule(netacl_id, rule_no):

    ec2 = boto3.resource('ec2')
    network_acl = ec2.NetworkAcl(netacl_id)

    response = network_acl.delete_entry(
        Egress=False,
        RuleNumber=rule_no
    )

    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        return True
    else:
        return False


def create_ddb_rule(netacl_id, host_ip, rule_no):

    ddb = boto3.resource('dynamodb')
    table = ddb.Table(ACLMETATABLE)
    timestamp = int(time.time())

    response = table.put_item(
        Item={
            'NetACLId': netacl_id,
            'CreatedAt': timestamp,
            'HostIp': str(host_ip),
            'RuleNo': str(rule_no)
            }
        )

    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        return True
    else:
        return False


def delete_ddb_rule(netacl_id, created_at):

    ddb = boto3.resource('dynamodb')
    table = ddb.Table(ACLMETATABLE)
    timestamp = int(time.time())

    response = table.delete_item(
        Key={
            'NetACLId': netacl_id,
            'CreatedAt': int(created_at)
            }
        )

    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        return True
    else:
        return False


#======================================================================================================================
# Lambda Entry Point
#======================================================================================================================


def lambda_handler(event, context):

    logger.info("log -- Event: %s " % json.dumps(event))

    try:

        if event["detail"]["type"] == 'Recon:EC2/PortProbeUnprotectedPort':
            SubnetId = event["detail"]["resource"]["instanceDetails"]["networkInterfaces"][0]["subnetId"]
            HostIp = event["detail"]["service"]["action"]["portProbeAction"]["portProbeDetails"][0]["remoteIpDetails"]["ipAddressV4"]
            instanceID = event["detail"]["resource"]["instanceDetails"]["instanceId"]
            NetworkAclId = get_netacl_id(subnet_id=SubnetId)

        else:
            SubnetId = event["detail"]["resource"]["instanceDetails"]["networkInterfaces"][0]["subnetId"]
            HostIp = event["detail"]["service"]["action"]["networkConnectionAction"]["remoteIpDetails"]["ipAddressV4"]
            instanceID = event["detail"]["resource"]["instanceDetails"]["instanceId"]
            NetworkAclId = get_netacl_id(subnet_id=SubnetId)

        if NetworkAclId:
            response = update_nacl(netacl_id=NetworkAclId,host_ip=HostIp)
        else:
            logger.info("Unable to determine NetworkAclId for instanceID: %s, HostIp: %s, SubnetId: %s. Confirm resources exist." % (instanceID, HostIp, SubnetId))
            pass

    except Exception as e:
        logger.error('Something went wrong.')
        raise