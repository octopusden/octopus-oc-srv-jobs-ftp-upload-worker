#!/usr/bin/env python3
"""
Logic for independent delivery processing. Allows to delay error raise to the end of upload
"""


import logging
from collections import namedtuple
from itertools import chain
from oc_delivery_apps.dlmanager.models import Client, FtpUploadClientOptions
from .ClientDeliverySender import EncryptingSender, SigningSender, MvnSender
from .upload_errors import DeliveryUploadError, ClientSetupError, EnvironmentSetupError
from .DeliveryDestinations import DeliveryDestinations

UploadResult = namedtuple("UploadResult", ("sent_deliveries", "raised_errors"))


def process_client_deliveries_independently(deliveries, client_sender):
    """ Runs upload for client's deliveries independently. Raises errors are returned but not raised 

    :param deliveries: list of deliveries to send
    :param client_sender: initialized ClientDeliverySender
    :returns: UploadResult with upload info
    """

    sent_deliveries = []
    raised_errors = []

    for delivery in deliveries:
        try:
            client_sender.send_delivery(delivery)
            sent_deliveries.append(delivery)
            logging.info(f"Successfully sent: [{delivery.gav}]")
        except DeliveryUploadError as exc:
            # ignore this single delivery and don't change its flags
            logging.error(f"Error uploading [{delivery.gav}]: {str(exc)}")
            raised_errors.append(exc)

    return UploadResult(sent_deliveries, raised_errors)


def process_clients_independently(deliveries, clients, context, repo_svn_fs, **kwargs):
    """ 
    Processes upload for each client and joins all results. Each clients gets ClientDeliverySender based on upload type (currently signed or encrypted)

    :param QuerySet deliveries: all deliveries to send
    :param list clients: list of clients to process. Each client will receive its portion of deliveries
    :param Context context:
    :param SvnFS repo_svn_fs: svn clients filesystem
    :param **kwargs: keyword arguments for resources initialization, see worker arguments description
    :return UploadResult: info for all deliveries
    """
    dd = DeliveryDestinations(config=kwargs['delivery_destinations_file'])
    upload_results = []
    client_errors = []

    for client in clients:

        try:
            upload_options = client.ftpuploadclientoptions
            can_receive, should_encrypt = upload_options.can_receive, upload_options.should_encrypt
        except FtpUploadClientOptions.DoesNotExist:
            # by default client receives encrypted deliveries
            can_receive, should_encrypt = True, True

        if not can_receive:
            logging.warning(f"[{client.code}] is marked as unreachable, skipping")
            continue

        try:
            client_deliveries = deliveries.filter(groupid__endswith=client.code)

            logging.debug(f'Checking if additional upload to MVN is required for [{client.code}]')

            for art in list(map(lambda x: x.get("artifactory"), dd.client_delivery_dest(client))):

                if not art:
                    continue

                logging.info(f'Performing additional upload to MVN for [{client.code}], repo: [{art}]')

                sender = MvnSender(client, context, dest=art, **kwargs)

                try:
                    art_client_result = process_client_deliveries_independently(client_deliveries, sender)
                except Exception as exc:
                    logging.error(f'Failed to upload to MVN: [{str(exc)}]')

            _ftp_enabled = True
            _ftp_dest = None

            for _ftp_d in list(map(lambda x: x.get("ftp"), dd.client_delivery_dest(client))):
                # provide target folder separately if may be discovered from DeliveryDestinations
                if not _ftp_d:
                    continue

                if not _ftp_d.get("enabled", True):
                    _ftp_enabled = False
                    
                _ftp_dest = _ftp_d
                break

            logging.info(f"FTP enabled for [{client.code}]: [{_ftp_enabled}]")

            if _ftp_enabled:
                if should_encrypt:
                    sender = EncryptingSender(client, context, repo_svn_fs=repo_svn_fs, dest=_ftp_dest, **kwargs)
                else:
                    sender = SigningSender(client, context, dest=_ftp_dest, **kwargs,)

                client_result = process_client_deliveries_independently(client_deliveries, sender)
            else:
                client_result = art_client_result

            logging.info(f"Sent to [{client.code}]: {len(client_result.sent_deliveries)}")
            upload_results.append(client_result)
        except ClientSetupError as exc:
            logging.error(f"Client [{client.code}] has configuration errors: [{str(exc)}]")
            client_errors.append(exc)

    result = UploadResult(list(chain.from_iterable([res.sent_deliveries for res in upload_results])),
                          list(chain.from_iterable([res.raised_errors for res in upload_results]))
                          + client_errors)
    logging.debug(f"Full upload result: [{result}]")
    return result
