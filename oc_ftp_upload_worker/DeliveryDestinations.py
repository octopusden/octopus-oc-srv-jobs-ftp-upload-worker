#!/usr/bin/env python3
import yaml
import logging
import os


class DeliveryDestinations (object):
    def __init__(self, config='delivery_destinations.yaml'):
        self.__config = os.path.abspath(config)
        logging.debug(f"Delivery destinations: [{self.__config}]")

        if not os.path.isfile(self.__config):
            logging.error(f"File not found: [{self.__config}], upload configuration will not be used")
            self.__config = None
            return

        with open(self.__config, mode='rt') as _stream:
            self.__config = yaml.load(_stream, Loader=yaml.Loader)

        logging.log(1, f'Dumping YAML: [{self.__config}]')

    def client_delivery_dest(self, client_code):
        """
        Get delivery destination by client code
        :param client_code: client code
        :return: delivery destination (ftp, artifactory) - as list
        """
        logging.debug(f'Reached client_delivery_dest, client_code: [{client_code}]')

        if not self.__config:
            logging.error("No upload configuration provided, default destination will be used only")
            return list()


        _result = self.__config.get(str(client_code))
        logging.debug(f'_result = [{_result}]')

        if not _result:
            # We have to return empty string if nothing found
            return list()

        return _result

