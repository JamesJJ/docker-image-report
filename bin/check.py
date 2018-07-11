#!/usr/bin/env python
# -*-  coding: utf-8 -*-

from __future__ import print_function

import sys
import os
import json
import base64
import urllib
import boto3
import docker
import requests
from requests.exceptions import RequestException
import logging
import re
import time
import string
import datetime
import random
import markdown
from string import Template
from pprint import pformat as pf


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        else:
            return json.JSONEncoder.default(self, obj)


def lh(name, level=os.environ.get('LOG_LEVEL', 'INFO')):
    logger = logging.getLogger(name)
    logger.setLevel('DEBUG')

    ch = logging.StreamHandler()
    ch.setLevel(level)

    # create formatter
    formatter = logging.Formatter('[%(levelname).4s] %(message)s')

    # add formatter to ch
    ch.setFormatter(formatter)

    # add ch to logger
    logger.addHandler(ch)
    return logger


def this_version_string():
    si = sys.implementation.name if hasattr(sys, 'implementation') else 'python_unknown'
    sv = sys.version_info
    return json.dumps({"python": """{0}-{1}.{2}.{3}-{4}-{5}""".format(si,
                                                                      sv.major,
                                                                      sv.minor,
                                                                      sv.micro,
                                                                      sv.releaselevel,
                                                                      sv.serial).lower(),
                       "config": str(os.getenv('APP_CONFIG_VERSION',
                                               'unknown'))},
                      indent=None,
                      sort_keys=True)


def receive_sqs(queue_url):

    try:
        sqs = boto3.client('sqs', region_name=os.environ.get('ECR_REGION', 'us-west-2'),
                           aws_access_key_id=os.environ.get('ECR_ACCESS_KEY', None),
                           aws_secret_access_key=os.environ.get('ECR_SECRET_KEY', None),
                           aws_session_token=os.environ.get('ECR_SESSION_TOKEN', None)
                           )
        events = sqs.receive_message(
            QueueUrl=queue_url,
            WaitTimeSeconds=20,
            MaxNumberOfMessages=10,
            VisibilityTimeout=4000,
            MessageAttributeNames=['All'])
    except Exception as e:
        logger.error(' = SQS Error: {}'.format(e))
        return

    rh = None
    quieter = False
    for event in events.get('Messages', []):
        try:
            logger.debug(' = Decapsulating JSON from Cloudwatch Event')
            body = json.loads(event.get('Body', '{}'))
            rh = event.get('ReceiptHandle')
            detail = body.get('detail', {})
            if verbose:
                logger.debug(pf(detail))
            if event_routing(detail) is True:
                sqs.delete_message(ReceiptHandle=rh, QueueUrl=queue_url)
                # Each SQS poll may contain up to 10 events
                # We don't need to see an info message for every SQS delete
                # ... so we log subsequent deletes at debug
                logger.log(logging.DEBUG if quieter else logging.INFO, ' = Deleting SQS ({!s})'.format(rh[4:12]))
                quieter = True
        except Exception as e:
            logger.error(e, exc_info=True)
            if rh:
                logger.warn(' = Deleting SQS ({!s})'.format(rh))
                sqs.delete_message(ReceiptHandle=rh, QueueUrl=queue_url)
            continue


def event_routing(input):
    rt = {
        "ecr.amazonaws.com": handle_ecr_global
    }
    func = rt.get(input.get('eventSource', ''), False)
    if func:
        return func(input)
    return True


def handle_ecr_global(input):
    if input.get('eventName', '_') not in ('PutImage'):
        return True
    if input.get('errorCode', '_') in ('ImageAlreadyExistsException'):
        logger.info(' = Image already exists ({!s})'.format(input.get('errorMessage', '')))
        return True

    if verbose:
        logger.debug(' = EcrInput: {}'.format(pf(input)))

    repo = input.get('requestParameters', {}).get('repositoryName')
    logger.info(' = Repo: {}'.format(pf(repo)))

    tag = input.get('requestParameters', {}).get('imageTag')
    logger.info(' = Tag: {}'.format(pf(tag)))

    regid = input.get('requestParameters', {}).get('registryId')
    logger.debug(' = RegId: {}'.format(pf(regid)))

    regregion = input.get('awsRegion', 'us-east-1')
    logger.debug(' = RegRegion: {}'.format(pf(regregion)))

    eventtime = input.get('eventTime', datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ'))
    logger.debug(' = EventTime: {}'.format(pf(eventtime)))

    useridbase = input.get('userIdentity', {})
    userid = useridbase.get('userName', useridbase.get('arn', '*Unknown*'))
    logger.debug(' = UserId: {}'.format(pf(userid)))

    handle_image(regid, regregion, repo, tag, userid, eventtime.replace('T', ' '))
    return True


def handle_image(
        regid,
        regregion,
        repo,
        tag,
        who=None,
        eventtime=datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ')):

    dryrun = True if os.environ.get('DRY_RUN', 'true') == 'true' else False

    with open('templates/report.txt.tmpl') as f:
        result_text = Template(f.read())
    f.closed

    with open('templates/report.html.tmpl') as f:
        htmlpage = Template(f.read().lstrip())
    f.closed

    registryaddress = os.environ.get(
        'ECR_REGISTRY_URL_FORMAT',
        '{regid}.dkr.ecr.{regregion}.amazonaws.com').format(
        regid=regid,
        regregion=regregion)
    imageaddress = '{}/{}'.format(
        registryaddress,
        repo
    )

    if str(tag) == '':
        tag = 'latest'

    if (tag == 'latest' or tag == ''):
        if os.environ.get('SKIP_UNTAGGED', 'true') == 'true':
            logger.info(' = Skipping "un-tagged" image: {}'.format(pf(repo)))
            return True

    delete = False
    deletereason = ''
    warnings = []

    # get ECR login
    ecrc = boto3.client('ecr',
                        region_name=regregion,
                        aws_access_key_id=os.environ.get('ECR_ACCESS_KEY', None),
                        aws_secret_access_key=os.environ.get('ECR_SECRET_KEY', None),
                        aws_session_token=os.environ.get('ECR_SESSION_TOKEN', None)
                        )
    ecrauth = ecrc.get_authorization_token(registryIds=[regid])
    if verbose:
        logger.debug(' = ECR Credentials: {}'.format(pf(ecrauth)))
    registryun, registrypw = base64.b64decode(
        ecrauth.get(
            'authorizationData', [])[0].get(
            'authorizationToken', 'user:pass')).decode('utf-8').split(':')

    logger.debug(
        ' = ECR Credentials: "{}" / "{}" (truncated to 12)'.format(registryun[:12], registrypw[:12]))

    dc = docker.DockerClient(base_url=os.environ.get('DOCKER_DAEMON', 'unix://var/run/docker.sock'))
    logger.debug(' = Docker Client: {}'.format(dc.version()))

    try:
        ecrlogin = dc.login(
            registry='https://{}'.format(registryaddress),
            username=registryun,
            password=registrypw,
            dockercfg_path='/dev/shm/.docker_config.json',
            # reauth=True,
        )
        logger.debug(' = ECR Login: {}\n{}'.format(registryaddress, pf(ecrlogin)))
    except (docker.errors.APIError) as e:
        logger.error(' = ECR Login: {}\n{}'.format(registryaddress, pf(e)))
        pass  # return False

    logger.info(' = Pulling: {}:{}'.format(imageaddress, tag))
    try:
        image = dc.images.pull(
            imageaddress,
            tag  # , auth_config={'username': registryun, 'password': registrypw}
        )
    except (docker.errors.APIError) as e:
        logger.error(' = Pull failed: {}'.format(e))
        return False
    except (docker.errors.ImageNotFound) as e:
        logger.warn(' = Pull failed: {}'.format(e))
        return True

    logger.debug(' = Image: {}'.format(pf(image, indent=4, width=1024, depth=None)))

    labels = image.labels
    if labels.get('owner_team', '') == '':
        warnings.append('**LABEL `owner_team` NOT FOUND**')
        delete = True
        deletereason = 'Missing label `owner_team`{}'.format(
            '' if deletereason == '' else ' | {}'.format(deletereason))
        logger.debug(' = Delete Reason: {}'.format(deletereason))

    hasjava = False
    try:
        javaversion = dc.containers.run(image.id,
                                        network_disabled=True,
                                        ipc_mode='none',
                                        remove=True,
                                        cpu_shares=768,
                                        detach=False,
                                        stdout=True,
                                        stderr=True,
                                        log_config={"type": "json-file"},
                                        entrypoint='java',
                                        command='-version'
                                        ).strip()
        hasjava = True
        javaversion = javaversion.decode('utf-8').split("\n")[0:3]
        logger.debug(' = Java: {}'.format(javaversion))
    except (docker.errors.ContainerError, docker.errors.APIError) as e:
        logger.debug(' = Java: {}'.format(pf(e)))
        javaversion = []

    if hasjava:
        javacgroupheap = False
        try:
            javacgroupheapout = dc.containers.run(image.id,
                                                  network_disabled=True,
                                                  ipc_mode='none',
                                                  remove=True,
                                                  cpu_shares=768,
                                                  detach=False,
                                                  stdout=True,
                                                  stderr=True,
                                                  log_config={"type": "json-file"},
                                                  entrypoint='java',
                                                  command='-XX:+UnlockExperimentalVMOptions -XX:+UseCGroupMemoryLimitForHeap -version'
                                                  ).strip().decode('utf-8').split("\n")[0:3]
            logger.debug(' = Java cGroup Heap: {}'.format(javacgroupheapout))
            javacgroupheap = True
        except (docker.errors.ContainerError, docker.errors.APIError) as e:
            logger.debug(' = Java cGroup Heap: {}'.format(pf(e)))
        javaversion.extend(
            ['Supports `-XX:+UseCGroupMemoryLimitForHeap`: {!s}'.format(javacgroupheap)])
        if javacgroupheap is False:
            warnings.extend(
                ['**JAVA is too old** ({!s}). Please update to support `-XX:+UseCGroupMemoryLimitForHeap`'.format(javaversion[0])])

    if hasjava:
        reactivemongo = None
        try:
            reactivemongo = dc.containers.run(
                image.id,
                network_disabled=True,
                ipc_mode='none',
                remove=True,
                cpu_shares=768,
                detach=False,
                stdout=True,
                stderr=False,
                log_config={
                    "type": "json-file"},
                entrypoint='sh',
                command='-c \'find / -type f -iname "*reactivemongo_*-*.jar" | head -n 1 || true\'').strip().decode('utf-8')
            logger.debug(' = ReactiveMongo find: {}'.format(reactivemongo))
            rmvmatch = re.search(r'[_\-](\d+)\.(\d+)\.(\d+)\.jar$', reactivemongo)
            if rmvmatch:
                logger.debug(
                    ' = ReactiveMongo: {0[0]}.{0[1]}.{0[2]}'.format(
                        rmvmatch.group(
                            1, 2, 3)))
                javaversion.extend(['ReactiveMongo: {0[0]}.{0[1]}.{0[2]}'.format(
                    rmvmatch.group(
                        1, 2, 3))])
                if rmvmatch.group(1) == '0' and int(rmvmatch.group(2)) < 13:
                    warnings.extend(['**REACTIVE-MONGO is too old** ({!s})'.format(
                        re.sub(r'^.*/', '', reactivemongo))])
                    delete = True
                    deletereason = 'ReactiveMongo {}{}'.format(
                        '{0[0]}.{0[1]}.{0[2]}'.format(rmvmatch.group(1, 2, 3)),
                        '' if deletereason == '' else ' | {}'.format(deletereason))
                    logger.debug(' = Delete Reason: {}'.format(deletereason))
        except (docker.errors.ContainerError, docker.errors.APIError) as e:
            logger.debug(' = ReactiveMongo: {}'.format(pf(e)))

    try:
        nodeversion = dc.containers.run(image.id,
                                        network_disabled=True,
                                        ipc_mode='none',
                                        remove=True,
                                        cpu_shares=768,
                                        detach=False,
                                        stdout=True,
                                        stderr=False,
                                        log_config={"type": "json-file"},
                                        entrypoint='node',
                                        command='--version'
                                        ).strip().decode("utf-8").split("\n")[0:1]
        logger.debug(' = Node: {}'.format(pf(nodeversion)))
        if re.search(r'^v?[0-79]\.', nodeversion[0]):
            warnings.extend(['**NODE is too old / EOL** ({!s})'.format(nodeversion[0])])
    except (docker.errors.ContainerError, docker.errors.APIError) as e:
        logger.debug(' = Node: {}'.format(pf(e)))
        nodeversion = []

    try:
        linuxversioncommand = r'''
           if [ -f /etc/alpine-release ]; then echo "ALPINE:$(cat /etc/alpine-release)";
           elif [ -f /etc/debian_version ]; then echo "DEBIAN:$(cat /etc/debian_version)";
           fi
           '''.strip().replace('\n', ' ')
        linuxversion = dc.containers.run(image.id,
                                         network_disabled=True,
                                         ipc_mode='none',
                                         remove=True,
                                         cpu_shares=768,
                                         detach=False,
                                         stdout=True,
                                         stderr=False,
                                         log_config={"type": "json-file"},
                                         entrypoint='sh',
                                         command='-c \'{}\''.format(linuxversioncommand)
                                         ).strip().decode("utf-8").split("\n")[0:1]
        logger.debug(' = Linux: {}'.format(pf(linuxversion)))
        if (re.search(r'^DEBIAN:[0-8]\.', linuxversion[0]) or
            re.search(r'^ALPINE:[0-2]\.', linuxversion[0]) or
                re.search(r'^ALPINE:3\.[0-5]\.', linuxversion[0])):
            warnings.extend(['**Linux distribution is old** ({!s})'.format(linuxversion[0])])
            warnings.extend(
                ['*2018/07 suggest using: `openjdk:8-jre-slim-stretch` / `node:10-stretch` / `debian:stretch-slim` / `alpine:3.8`*'])
    except (docker.errors.ContainerError, docker.errors.APIError) as e:
        logger.warn(' = Linux: {}'.format(pf(e)))
        linuxversion = []


    try:
        alpineversions = r'''
           apk --no-cache version
           '''.strip().replace('\n', ' ')
        apkversion = dc.containers.run(image.id,
                                         network_disabled=False,   # THIS NEEDS NETWORK
                                         ipc_mode='none',
                                         remove=True,
                                         cpu_shares=768,
                                         detach=False,
                                         stdout=True,
                                         stderr=False,
                                         log_config={"type": "json-file"},
                                         entrypoint='sh',
                                         command='-c \'{}\''.format(alpineversions)
                                         ).strip().decode("utf-8").split("\n")
        logger.debug(' = APK: {}'.format(pf(apkversion)))
    except (docker.errors.ContainerError, docker.errors.APIError) as e:
        logger.warn(' = APK: {}'.format(pf(e)))
        apkversion = []

    apkvloop_msg_done = False
    for apkv in apkversion:
      if re.search(r'^openjdk8\-j.+< 8\.171\.', apkv):
        logger.debug(' = APK: Qualified for delete: {}'.format(pf(apkv)))
        delete = True
        if apkvloop_msg_done is False:
            javaversion.extend(['**OPENJDK APK package is very out of date:** {!s}'.format(apkv.strip())])
            apkvloop_msg_done = True
            warnings.append('**Alpine packages not updated**')
            deletereason = 'Build process issues: Up to date base image not pulled. Critical OS packages not updated{}'.format(
                '' if deletereason == '' else ' | {}'.format(deletereason))
            logger.debug(' = Delete Reason: {}'.format(deletereason))


    history = []
    for layer in image.history():
        cmd = re.sub(
            '([^\n]{100}) ',
            "\\1\n  ",
            "\n  &&".join(
                (";\n".join(
                    re.sub(
                        '[ \r\t]+',
                        ' ',
                        layer.get(
                            'CreatedBy',
                            '').strip()).split(';'))).split('&&'))).strip()
        history.extend(
            ["##### {!s}, Size: {:,} MB\n\n\n```\n{!s}\n```\n\n\n".format(
                datetime.datetime.utcfromtimestamp(int(layer.get('Created', 0))).strftime('%Y-%m-%d %H:%M:%SZ'),
                int(layer.get('Size', 0) / 1024 / 1024),
                cmd,
            )]
        )

    try:
        dc.images.remove(image=image.id, force=True)
    except (docker.errors.APIError) as e:
        logger.debug(' = Remove image: {}'.format(pf(e)))

    if len(warnings) > 0:
        logger.debug(' = WARNINGS: \n{}'.format(to_md_bullets(warnings)))

    colours = {'ok': '#98ef8d', 'warn': '#ffd000', 'concern': '#ff7400', 'critical': '#ff003e'}
    pretty_who = labels.get('owner_team', who[:24] if re.search(
        r'^[a-zA-Z0-9_\.]+$', '' if who is None else who) else '')

    reportmarkdown = result_text.safe_substitute(
        image=to_md_bullets(['`{}:{}`'.format(repo, tag), '_{!s}_'.format(image.short_id)]),
        time=eventtime,
        who='{unknown}' if who == '' else '{!s}'.format(who),
        labels='{none}' if labels == {} else to_md_bullets(labels, '`'),
        java='{none}' if javaversion == [] else to_md_bullets(javaversion),
        node='{none}' if nodeversion == [] else to_md_bullets(nodeversion),
        os='{unknown}' if linuxversion == [] else to_md_bullets(linuxversion),
        history="\n\n".join(history),
        action='ACCEPT' if delete is False else to_md_bullets(['{}DELETED ({!s})'.format('*[DRYRUN] Would have been:* ' if dryrun else '', deletereason)]),
        warnings='{none}' if warnings == [] else to_md_bullets(warnings),
    )

    finalhtml = htmlpage.safe_substitute(
        LOGO_URL=os.environ.get(
            'LOGO_URL',
            'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAQAAADZc7J/AAACgklEQVRIx42VT0hUURTGv7SBnMkIcSKpnCEoW8wuhEgIXQTlGLjTlbQxRgj6J4xLhUgHFy3ClUSJr5ULCQqajc5GYtzUasaNGL2wxaBggyYOzq/F/HvP9940567eeef77jnn3vsd6YSFhBTsiY4lppPzGcNcMF9m+pOBhKIKqr5dULNQa3g0vpj+ns9xyDEABbZZ4GZeacUV9oQ/V0QKDMeWstsUcdomDxDKKqaACxwNKBCaMjYKeJpJD0IFGeo8AX+mAXXcmEvtUt++0IoQSqnLVntEgdBcKu8COeLI8vWX/hIBSlmyaJYCU4bb7qsMMsiqxTNbIUBGtRdoeMyt9hzdCNFNrupb5nSFoKBY+dxbw0tZt4q3uIwQV9iq+j7jq+WQVVgSGo1vu7bsiHFaaGHc0of3nKoRoLgkBRfTRY+u77PCCvsWz2MrHKUVVE/0W54G7QfX7QR5RTWWyDUILzJhhyOU0HTysEGCd5xzEiQ1nzluALzDNOedcJSR8csbtofJJl95zW2a3ODI9CA4Zo0n3OIql9wStxK4lbDLBG31YNYSnE3c42Fj4FITncf4yn7b6q8Zx0XaINQ4PK+o4yq/sQT46aMPvzdBWsETj6nISPW3j1kOOGDW+gLtK+54zkc1zSGMCcBPwu7wbFmj0VCsIigFBqsB7awDsE67G7wiKCVJmzR2yjm8sAT1sswyve771yQtqIj8nRVR/cQZS5jPq/6UTdqfakAdXXOpHeAP9/5/fHZZrwwWf+eksVGAtbIWeiy3wVLKIiIFhmJL2d985JoX3Gu0lXrRJHQ2/Cj+If02f582mu23rv5wtY/3u9GRxJ3kxYzPlKmMkprxGu//AKfspPPYrnsnAAAAAElFTkSuQmCC'),
        CSS_URL=os.environ.get(
            'CSS_URL',
            'https://cdn.rawgit.com/yegor256/tacit/gh-pages/tacit-css-1.2.9.min.css'),
        REPORT_HTML=markdown.markdown(
            reportmarkdown,
            output_format='html5',
            extensions=['markdown.extensions.fenced_code']))
    try:
        url = put_report_s3_presign(finalhtml)
    except Exception as e:
        logger.error(' = put_report_s3_presign: {}'.format(pf(e)))
        url = None

    #delete = False
    #warnings = []

    at_title = '{}:{}  {}{}'.format(repo, tag, '' if pretty_who == '' else '@', pretty_who)
    at_colour = colours['concern'] if (delete and dryrun) else colours['critical'] if delete else colours['warn'] if len(
        warnings) > 0 else colours['ok']
    at_summary = '{}: {}:{}'.format('Deleted' if delete else 'Warning' if len(
        warnings) > 0 else 'Accepted', repo, tag)
    at_msg = '{}**Image deleted** ({})\n\n`{}`'.format(
        '*[DRYRUN] Would have been:* ' if dryrun else '',
        deletereason, who) if delete else '**Warning:**\n\n{}'.format(
        to_md_bullets(warnings)) if len(warnings) > 0 else ''

    try:
        teams_webhooks_delete = json.loads(os.environ.get('TEAMS_DELETE_URLS', []))
        teams_webhooks_warning = json.loads(os.environ.get('TEAMS_WARNING_URLS', []))
        teams_webhooks_ok = json.loads(os.environ.get('TEAMS_OK_URLS', []))
    except Exception as e:
        logger.error(' = Error parsing Teams URLs: {}'.format(e))

    teams_webhooks = teams_webhooks_delete if delete else teams_webhooks_warning if len(warnings) > 0 else teams_webhooks_ok

    alert_teams(at_title, at_colour, at_summary, at_msg, teams_webhooks, url)


    if (delete and not dryrun):
        try:
            logger.debug(' = ECR Delete: {}'.format(image.id))
            del_tag = ecrc.batch_delete_image(
                registryId=regid,
                repositoryName=repo,
                imageIds=[
                    {
                        'imageTag': tag,
                    },
                ]
            )
            logger.debug(' = ECR DeleteTag: {}'.format(pf(del_tag)))
            del_id = ecrc.batch_delete_image(
                registryId=regid,
                repositoryName=repo,
                imageIds=[
                    {
                        'imageDigest': image.id,
                    },
                ]
            )
            logger.debug(' = ECR DeleteId: {}'.format(pf(del_id)))
        except Exception as e:
            logger.error(' = ECR Delete Error: {}'.format(e))



def put_report_s3_presign(finalhtml):
    s3bucket = os.environ.get('REPORT_BUCKET', 's3-bucket.example.com')
    s3key = 'report/{}{}.html'.format(rid(4), datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S'))
    s3c = boto3.client('s3', region_name='us-west-2')
    response = s3c.put_object(
        ACL='bucket-owner-full-control',
        Body=finalhtml.encode('utf-8'),
        Bucket=s3bucket,
        CacheControl='private, no-cache, no-store',
        ContentType='text/html',
        Key=s3key,
        ServerSideEncryption='AES256',
        StorageClass='STANDARD'
    )
    psurl = s3c.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': s3bucket,
            'Key': s3key},
        ExpiresIn=86400 * 7)
    logger.info(' = REPORT: {}'.format(psurl))
    return psurl


def rid(size=6, chars=string.ascii_uppercase):
    return ''.join(random.choice(chars) for _ in range(size))


def to_md_bullets(d, p='', i=1, bullet='*'):
    s = ''
    if isinstance(d, list):
        for v in d:
            s += "{}{} {}{!s}{}\n".format(' ' * i, bullet, p, v, p)
    elif isinstance(d, dict):
        for k, v in d.items():
            s += "{}{} {!s:16s}: {}{!s}{}\n".format(' ' * i, bullet, k, p, v, p)
    return s


def alert_teams(
        title,
        colour='',
        summary='',
        msg='',
        teams_webhooks=[],
        url=None,
        ms_teams_proxy=None):

    body = msg

    try:
        body = body.decode('utf-8')
    except AttributeError:
        pass
    try:
        title = title.decode('utf-8')
    except AttributeError:
        pass
    try:
        summary = summary.decode('utf-8')
    except AttributeError:
        pass
    try:
        url = url.decode('utf-8')
    except AttributeError:
        pass

    headers = {'content-type': 'application/json'}
    # set https proxy, if it was provided
    proxies = {'https': ms_teams_proxy} if ms_teams_proxy else None
    payload = {'@type': 'MessageCard',
               '@context': 'http://schema.org/extensions',
               'summary': summary,
               'title': title,
               'text': body,
               "potentialAction": [{"@type": "OpenUri",
                                    "name": "Show Full Report (available until {})".format(re.sub(r'(^| )0',
                                                                                                  r'\1',
                                                                                                  (datetime.datetime.utcnow() + datetime.timedelta(days=7)).strftime("%m/%d %I%p").lower())),
                                    "targets": [{"os": "default",
                                                 "uri": url}]}]}
    if colour != '':
        payload['themeColor'] = colour

    for url in teams_webhooks:
        try:
            response = requests.post(
                url,
                data=json.dumps(
                    payload,
                    cls=DateTimeEncoder),
                headers=headers,
                proxies=proxies)
            response.raise_for_status()
        except RequestException as e:
            logger.error("Error posting to ms teams: %s" % e)
        logger.debug(' = Teams response: {!s}'.format(response))


if __name__ == '__main__':

    verbose = False

    logger = lh(__name__)

    logger.info('STARTING: {0}'.format(this_version_string()))

    sqs_queue = os.environ.get(
        'SQS_QUEUE_URL',
        None)

    while True:
        time.sleep(0.1)
        receive_sqs(sqs_queue)
