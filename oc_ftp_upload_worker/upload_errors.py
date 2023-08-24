

class UploadProcessException(Exception):
    """ Base exception for all errors which can occur during deliveries upload """
    pass


class DeliveryUploadError(UploadProcessException):
    """ Error occured while working with single delivery """
    pass


class DeliveryExistsError(DeliveryUploadError):
    """ Delivery wasn't uploaded due to existing archive with same name """
    pass


class DeliveryEncryptionError(DeliveryUploadError):
    """ Delivery wasn't uploaded because encryption has failed """
    pass


class ClientSetupError(UploadProcessException):
    """ Error in external resource related to client """
    pass


class EnvironmentSetupError(UploadProcessException):
    """ Error in whole upload configuration """
    pass
