# PSQL-based FTP/MVN upload worker

This worker listens to AMQP-based queue and does a delivery synchronization with *FTP* or/and external *MVN* storage.

## Binary packages required
- *GnuPG* - for gpg encryption and signing

## Environment variables

- *PSQL\_URL*, *PSQL\_USER*, *PSQL\_PASSWORD* - credentials for *PSQL* database (*dlmanager* Django application). *PSQL\_URL* should contain database schema. Format: *hostFQDN*:*port*/*instance*?search\_path=*schema*
- *MVN\_URL*, *MVN\_USER*, *MVN\_PASSWORD* - general credentials for *maven*-like repository connection. Will be used if not overrided with any of two groups below
- *MVN\_EXT\_URL*, *MVN\_EXT\_USER*, *MVN\_EXT\_PASSWORD* - credentials for **external** (available for client) instance of *maven*-like repository.
- *MVN\_INT\_URL*, *MVN\_INT\_USER*, *MVN\_INT\_PASSWORD* - credentials for **internal** (not available for client) instance of *maven*-like repository.
- *MVN\_DOWNLOAD\_REPO* - MVN repository do download deliveries from
- *MVN\_LINK\_URL* - URL for delivery hyperlink construction (for customer)
- *AMQP\_URL*, *AMQP\_USER*, *AMQP\_PASSWORD* - credentials for *AMQP* server connection.
- *WORKER\_QUEUE* - a queue to listen, default: `cdt.dlupload.input`
- *FTP\_URL*, *FTP\_USER*, *FTP\_PASSWORD* - credentials for *FTP* server connection.
- *SVN\_CLIENTS\_URL*, *SVN\_CLIENTS\_USER*, *SVN_CLIENTS_PASSWORD* - credentials for Subversion PGP keys storage (for recipients)
- *SMTP\_USER*, *SMTP\_PASSWORD*, *SMTP\_URL* - *SMTP* server credentials - for senging e-mails about deliveries.
- *MAIL\_DOMAIN* - mail domain for sending e-mails from. Will be used as `From: noreply@${MAIL_DOMAIN}`
- *MAIL\_FROM* - address (may be with skipped domain) to send e-mail notifications from. **Should be specified as USER_ID in private key**
- *MAIL\_CONFIG\_FILE* - path to mailer configuration file
- *PGP\_CHECK* - Enable (`True`) or Disable (`False`) PGP private key check, default: `True`
- *PGP\_PRIVATE\_KEY\_PASSWORD* - credentials for *PGP/GPG* private key. Used for encryption or signing.
- *PGP\_PRIVATE\_KEY\_FILE* - path to *PGP/GPG* private key *on local filesystem*. **Should contain both private and public part.**
- *PGP\_MAIL\_FROM* - address (may be with skipped domain) to search encryption key (i.e. used as *KEY\_ID* in *PGP\_PRIVATE\_KEY\_FILE*)
- *DELIVERY\_DESTINATIONS\_FILE* - path for *delivery\_destinations.yml* settings file. See format below.

