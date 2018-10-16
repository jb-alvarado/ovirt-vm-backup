# ovirt-vm-backup

This tool makes full online backup of every given VM. It uses different configuration files, one global config for server wide configurations. And another one piped as argument for VM specific settings.

## Requirements:
+ Python >= 3.5
+ pip
+ the *ca.pem* file from oVirt Engine

## Installation:
+ clone folder to **/usr/local/ovirt-vm-backup**
+ cd **/usr/local/ovirt-vm-backup**
+ mkdir **venv**
+ virtualenv -p python3 venv
+ source **venv/bin/activate**
+ pip install -r requirements.txt
+ symlink **ovirt-vm-backup.conf** to **/etc/ovirt-vm-backup/ovirt-vm-backup.conf** (optional)
+ edit **ovirt-vm-backup.conf** to your needs (don't forget the path from ca.pem)
+ create **/etc/ovirt-vm-backup/preferred_name.cfg** based on **example-config.cfg** with the right settings
+ mkdir **/var/log/ovirt-vm-backup**
+ chown user. /var/log/ovirt-vm-backup
+ chown user. /etc/ovirt-vm-backup/ovirt-vm-backup.conf
+ chmod 600 /etc/ovirt-vm-backup/ovirt-vm-backup.conf

## Functionality
The oVirt API works mostly [asynchronous](https://ovirt.org/blog/2017/05/higher-performance-for-python-sdk/), but this script runs most commands sequential. That means, that the runtime is longer, because every operation waits for the previous one to be done. This is important, because we can not clone a VM from snapshot when the snapshot is not fully created before... At the moment only three functions a asynchronous. That is deleting old backups, the backup snapshot and the temporary VM.

To backup VMs the script runs this commands in sequence:
+ check config integrity
+ check storage space and VM size
+ if there is enough space:
    + check for old Backups, if they are older then specified - delete them
    + create a snapshot
    + clone the snapshot to a temporary VM
    + delete the snapshot
    + copy the created VM to the export domain
    + delete the temporary VM


## Cronjob:

Edit **/etc/crontab** and add:

```shell
15 0     * * 1   user    /usr/local/ovirt-vm-backup/venv/bin/python /usr/local/ovirt-vm-backup/ovirt-vm-backup.py -c /etc/ovirt-vm-backup/preferred_name.cfg

# add more backup jobs with different times and *.cfg files
```

## Usefull links:
+ [Python oVirt SDK Examples](https://github.com/oVirt/ovirt-engine-sdk/tree/master/sdk/examples)

## Install on CentOS 7.5:
+ to use the python3 virtual environment, we need to install first python3:
`yum install rh-python36`
+ and we need also: `libcurl-devel gcc libxslt-devel libxml++-devel libxml2-devel openssl-devel`
+ now python3 is not fully usable, first we have to activate it:
`scl enable rh-python36 bash`
+ after this we can create our virtual environment
+ the *ovirt-engine-sdk-python* installation can still make problems, because of pycurl - so we install it by hand:
    + `export PYCURL_SSL_LIBRARY=nss`
    + `pip install --no-cache-dir --compile --ignore-installed --install-option="--with-nss" pycurl`
+ now we can install the *ovirt-engine-sdk-python* package
