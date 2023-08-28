#!/usr/bin/env python3
""" Some utility functions used during upload """


import logging
from oc_delivery_apps.checksums.controllers import CheckSumsController
from oc_delivery_apps.checksums.models import Locations, LocTypes
from oc_delivery_apps.dlmanager.models import Client, Delivery
from .upload_errors import DeliveryUploadError, ClientSetupError, EnvironmentSetupError
import os
from copy import deepcopy
from .DeliveryDestinations import DeliveryDestinations
from oc_cdtapi.NexusAPI import gav_to_path, parse_gav
from string import Template


def get_pending_deliveries():
    """ Retrieves deliveries that should be uploaded from database. Checks technical status (only flag_approved should be set) and history (deliveries with archive removed from Nexus are skipped)

    :return QuerySet: pending deliveries
    """
    pending_deliveries = Delivery.objects.filter(flag_approved=True, flag_uploaded=False, flag_failed=False)
    sendable_deliveries = filter(_is_sendable, pending_deliveries)
    # convert it back to QuerySet for easier further usage
    sendable_ids = [dlv.pk for dlv in sendable_deliveries]
    unsendable_deliveries = pending_deliveries.exclude(pk__in=sendable_ids)
    logging.warning(f"Deliveries are unsendable: {', '.join([dlv.gav for dlv in unsendable_deliveries])}")
    processed_deliveries = pending_deliveries.filter(pk__in=sendable_ids)
    return processed_deliveries


def _is_sendable(delivery):
    """
    Check if delivery can be send
    :param dlmanager.Delivery delivery: delivery record
    :return bool: may be send or not
    """
    controller = CheckSumsController()
    exists_now = bool(controller.get_file_by_location(delivery.gav, "NXS", history=False))
    existed_ever = bool(controller.get_file_by_location(delivery.gav, "NXS", history=True))
    # we only want to exclude files with explicit location removal
    # if file wasn't found at all, it may be because of old delivery without file registered
    was_deleted = existed_ever and not exists_now
    return not was_deleted


def notify_deliveries_recipients(mailer, clients, deliveries, **kwargs):
    """ 
    Sends upload notifications to clients
    :param oc_mailer.Mailer mailer: mailer instance
    :param list clients: list of clients to notify
    :param QuerySet deliveries: list of delivery records from database
    """
    for client in clients:
        # plain list is passed here, so we can only use regular filter syntax
        client_deliveries = list(filter(lambda dlv: dlv.groupid.endswith(client.code),
                                   deliveries))
        notify_client(mailer, client, client_deliveries, **kwargs)


def notify_client(mailer, client, deliveries, **kwargs):
    """
    Sends upload notifications to single client.

    :param oc_mailer.Mailer mailer: Mailer instance
    :param str client: client to send notification
    :param list deliveries: list of deliveries (as database records) to mention. Should belong to given client
    :param str delivery_destinations_file: path to delivery_destinations configuration
    :param str mail_domain: mail domain to append obtain to author`s e-mail
    :param str external_repo_prefix_url_tmpl: template for external link
    :param **kwargs: keyword arguments fopr substitute in template link (lowercase)
    """
    other_client_deliveries = list(filter(lambda dlv: dlv.client_name != client.code, deliveries))

    if other_client_deliveries:
        raise ValueError(f"Attempted to notify about other client deliveries: {other_client_deliveries}")

    mailer_to = list(client.clientemailaddress_set.all().values_list("email_address", flat=True))
    delivery_dest = DeliveryDestinations(config=kwargs['delivery_destinations_file'])

    logging.debug(f"Try to send mail to [{client.code}], deliveries: [{deliveries}]")

    for delivery in deliveries:
        mailer_to_ = deepcopy(mailer_to)

        try:
            subject = "-".join([delivery.artifactid, delivery.version])
            logging.debug(f"Mail subject: [{subject}]")
            text = f'Delivery {subject} has been uploaded'
            logging.debug(f"Mail text: [{text}]")
            _client_repo  = delivery_dest.client_delivery_dest(delivery.client_name)
            logging.debug(f"Client repo from dd: [{_client_repo}]")
            _client_repo = list(filter(lambda x: bool(
                isinstance(x.get("artifactory"), dict) and x.get("artifactory").get("target_repo")), _client_repo))

            logging.debug(f"Client repo after filter: [{_client_repo}]")

            if _client_repo:
                _client_repo = _client_repo.pop(0).get("artifactory").get("target_repo")

            if _client_repo:
                logging.debug(f"MVN upload was enabled: [{_client_repo}]")
                _dict_subst = os.environ.copy()
                _dict_subst.update(kwargs)
                _dict_subst.update({k.upper(): v for k, v in kwargs.items()})

                # exclude credentials
                for _d in ["_user", "_password", "_token"]:
                    for _k in list(filter(lambda _x: _x.lower().endswith(_d), _dict_subst.keys())):
                        del(_dict_subst[_k])

                _dict_subst['CLIENT_REPO'] = _client_repo
                _dict_subst['FULL_GAV'] = gav_to_path(delivery.gav)
                _dict_subst.update({k.upper(): v for k, v in parse_gav(delivery.gav).items()})
                _template_url_ = _dict_subst['EXTERNAL_REPO_PREFIX_URL_TMPL']
                _template_url_result = Template(_template_url_).safe_substitute(_dict_subst)
                text += f'<br> Download URL: <a href="{_template_url_result}">{_template_url_result}</a>'
                logging.debug(f"Mail text: {text}")

            if delivery.mf_delivery_author:
                mailer_to_.append('@'.join([delivery.mf_delivery_author, kwargs['mail_domain']]))

            logging.debug(f"Mail to: [{mailer_to_}]")

            if not mailer_to_:
                logging.info(f"No notifications email specified for [{client.code}]")
            else:
                logging.info(f"Sending notification to [{mailer_to_}], subject [{subject}], text [{text}]")
                mailer.send_email(to_addresses=mailer_to_, subject=subject, text=text)

        except Exception as exc:
            # ignore fail to send notification
            logging.error(f"Notification was not sent to [{client.code}]: [{str(exc)}]")
