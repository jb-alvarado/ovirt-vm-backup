#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import logging
import os
import smtplib
import socket
import sys
from argparse import ArgumentParser
from ast import literal_eval
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import TimedRotatingFileHandler
from time import sleep
from types import SimpleNamespace

import ovirtsdk4 as sdk
import ovirtsdk4.types as types

# ------------------------------------------------------------------------------
# read global variables from config file
# ------------------------------------------------------------------------------

cfg = configparser.ConfigParser()
if os.path.exists("/etc/ovirt-vm-backup/ovirt-vm-backup.conf"):
    cfg.read("/etc/ovirt-vm-backup/ovirt-vm-backup.conf")
else:
    cfg.read("ovirt-vm-backup.conf")

_MAIL = SimpleNamespace(
    server=cfg.get('MAIL', 'smpt_server'),
    port=cfg.getint('MAIL', 'smpt_port'),
    s_addr=cfg.get('MAIL', 'sender_addr'),
    s_pass=cfg.get('MAIL', 'sender_pass'),
    recip=cfg.get('MAIL', 'recipient')
)

_LOG = SimpleNamespace(
    path=cfg.get('LOGGING', 'log_file'),
    level=cfg.get('LOGGING', 'log_level')
)

_API = SimpleNamespace(
    url=cfg.get('API', 'api_url'),
    user=cfg.get('API', 'api_user'),
    pw=cfg.get('API', 'api_password'),
    ca=cfg.get('API', 'api_ca_file'),
    app=cfg.get('API', 'application_name')
)


# ------------------------------------------------------------------------------
# read config with VM list and vm specific settings
# ------------------------------------------------------------------------------

def create_argparser():
    args = ArgumentParser()
    # General options
    args.add_argument(
        "-c", "--config-file",
        help="Path to the config file, pass dash (-) for stdin",
        dest="config_file",
        required=True,
    )

    return args


# ------------------------------------------------------------------------------
# logging
# ------------------------------------------------------------------------------

logger_path = _LOG.path
logger = logging.getLogger(__name__)
logger.setLevel(_LOG.level)
handler = TimedRotatingFileHandler(logger_path, when="midnight", backupCount=5)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s]  %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# ------------------------------------------------------------------------------
# read vm specific variables from config file
# ------------------------------------------------------------------------------

parser = create_argparser()
options = parser.parse_args(sys.argv[1:])
config_file = configparser.ConfigParser()

if os.path.exists(options.config_file):
    config_file.read(options.config_file)
else:
    logger.error('File "{}" not exists!'.format(options.config_file))
    sys.exit(1)

VM_LIST = literal_eval(config_file.get('VMS', 'vm_list'))
VM_MIDDLE = config_file.get('VMS', 'vm_middle')
RAM_STATE = config_file.getboolean('VMS', 'persist_memorystate')
MAX_OPERATION_TIME = config_file.getint('VMS', 'max_operation_time')
HOLD_BACKUPS = config_file.getint('VMS', 'hold_backups')
CLUSTER = config_file.get('CLUSTER', 'cluster_name')
STORAGE_DOMAIN = config_file.get('CLUSTER', 'storage_domain')
EXPORT_DOMAIN = config_file.get('CLUSTER', 'export_domain')
LOW_SPACE_INDICATOR = config_file.getint('CLUSTER', 'low_space_indicator')


# ------------------------------------------------------------------------------
# Send Mails with message
# ------------------------------------------------------------------------------

def send_mail(message):
    if _MAIL.recip:
        sender = _MAIL.s_addr
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = _MAIL.recip
        msg['Subject'] = "oVirt VM Backup"
        msg.attach(MIMEText(
            '{}'.format(message), 'html', 'utf-8'))
        text = msg.as_string()

        try:
            server = smtplib.SMTP(_MAIL.server, _MAIL.port)
        except socket.error as err:
            logger.error(err)
            server = None

        if server is not None:
            server.starttls()
            try:
                login = server.login(sender, _MAIL.s_pass)
            except smtplib.SMTPAuthenticationError as serr:
                logger.error(serr)
                login = None

            if login is not None:
                server.sendmail(sender, _MAIL.recip, text)
                server.quit()


# ------------------------------------------------------------------------------
# Main Program Helper
# ------------------------------------------------------------------------------

# Create the connection to the server:
connection = sdk.Connection(
    url=_API.url,
    username=_API.user,
    password=_API.pw,
    ca_file=_API.ca,
    debug=True,
)


# validate connection to the engine and all values in the config file
class CheckConfigIntegrity(object):
    def __init__(self, vm_list, cluster, storage_domain, export_domain):
        self.vm_list = vm_list
        self.cluster = cluster
        self.storage_domain = storage_domain
        self.export_domain = export_domain

        try:
            self.test_connection()
            self.test_config_values()
            self.test_vm_names()
        except ValueError as err:
            logger.error(err)
            send_mail(err)
            connection.close()
            sys.exit(1)

    def test_connection(self):
        if not connection.test(raise_exception=False):
            raise ValueError("Connection to engine doesn't work!")

    def test_config_values(self):
        system_service = connection.system_service()
        cls_service = system_service.clusters_service()
        sds_service = system_service.storage_domains_service()

        # test if cluster exists
        if not cls_service.list(search='name={}'.format(self.cluster)):
            raise ValueError("Cluster: '{}' doesn't exists!".format(
                self.cluster))

        # test if storage domain exists
        if not sds_service.list(search='name={}'.format(self.storage_domain)):
            raise ValueError("Storage domain: '{}' doesn't exists!".format(
                self.storage_domain))

        # test if export domain exists
        if not sds_service.list(search='name={}'.format(self.export_domain)):
            raise ValueError("Export domain: '{}' doesn't exists!".format(
                self.export_domain))

    def test_vm_names(self):
        vms_service = connection.system_service().vms_service()

        for vm in self.vm_list:
            if not vms_service.list(search='name={}'.format(vm)):
                log = "The VM '{}' doesn't exists on your cluster!".format(vm)
                logger.error(log)
                send_mail(
                    "The VM <b>{}</b> doesn't exists on your cluster!".format(
                        vm)
                    )


# human readable file size
def sizeof_fmt(num):
    for unit in ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB']:
        if abs(num) < 1024.0:
            return "%3.2f%s" % (num, unit)
        num /= 1024.0


# test if value is an integer
def is_int(value):
    try:
        return int(value)
    except ValueError:
        return False


# test if value is a date string
def is_date(value):
    try:
        return datetime.strptime(value, '%Y%m%d')
    except ValueError:
        return False


# search for old backups on export domain,
# and if they older then HOLD_BACKUPS time, delete them
def check_old_backups(system_service, vms_service, vm):
    sds_service = system_service.storage_domains_service()
    sd = sds_service.list(search='name={}'.format(EXPORT_DOMAIN))[0]
    sd_service = sds_service.storage_domain_service(sd.id)
    sd_vms_service = sd_service.vms_service()

    vms_list = sd_vms_service.list()

    search_date = datetime.now() - timedelta(HOLD_BACKUPS)
    expired_date = search_date.strftime('%Y%m%d')

    # list all VMs in backup domain
    # search for the correct backup name and check if the backup is expired
    for vme in vms_list:
        if '{}_{}'.format(vm.name, VM_MIDDLE) in vme.name:
            # get the last 15 caracters from our backup name,
            # this should include date and time
            date_str, time_str = vme.name[-15:].split('_')
            dt = is_int(date_str)
            tm = is_int(time_str)

            # we check all time and date strings, only to be sure that this
            # is really our own backup made from this script
            if dt and tm and is_date(date_str) and dt < int(expired_date):
                logger.info('Delete old backup: {}'.format(vme.name))

                exported_vm_service = sd_vms_service.vm_service(vme.id)
                exported_vm_service.remove()


# calculate the free space from the given storage domain
def get_storage_free_space(system_service, domain):
    sds_service = system_service.storage_domains_service()
    sd = sds_service.list(search='name={}'.format(domain))[0]
    sd_service = sds_service.storage_domain_service(sd.id)

    # get percentage from our warning indicator
    if LOW_SPACE_INDICATOR > 0:
        space_indicator = LOW_SPACE_INDICATOR
    else:
        space_indicator = sd_service.get().warning_low_space_indicator

    # full size from storage domain
    storage_size = sd_service.get().used + sd_service.get().available

    # space_indicator in byte
    size_indicator = storage_size * space_indicator / 100
    # available size for our backup VM
    free_space = sd_service.get().available - size_indicator

    logger.info(
        "Free space on domain: '{}' is: {}".format(
            domain, sizeof_fmt(free_space)))

    return int(free_space)


# calculate the size from all VM disks
def get_vm_disks_size(vms_service, vm):
    vm_service = vms_service.vm_service(vm.id)
    disk_attachments_service = vm_service.disk_attachments_service()
    disk_attachments = disk_attachments_service.list()
    snaps_service = vm_service.snapshots_service()

    disks_size = 0

    # list all attached disks and add the size
    for disk_attachment in disk_attachments:
        disk = connection.follow_link(disk_attachment.disk)
        disks_size += disk.actual_size

    # list all snapshots and get there size
    for snap in snaps_service.list():
        snap_service = snaps_service.snapshot_service(snap.id)
        disks_service = snap_service.disks_service()

        for disk in disks_service.list():
            disks_size += disk.actual_size

    logger.info(
        "[{}] disks size is: {}".format(vm.name, sizeof_fmt(disks_size)))

    return int(disks_size)


# search in the VM for an backup snapshot
# when wait_for_state is True, this process switch to a sequential behavior
def check_snapshot(snap, snapshots_service, wait_for_state=False):
    if snap is None:
        # list all snapshots from the vm, and search for the backup snapshot
        for snapshot in snapshots_service.list():
            if snapshot.description == "snapshot for backup":
                snap = snapshot
                break

    # when we have found the snapshot:
    # check the state, if is locked it means the snapshot is in creation
    # if state is ok return the snapshot
    if snap is not None and wait_for_state:
        snap_service = snapshots_service.snapshot_service(snap.id)
        counter = 0

        while snap.snapshot_status != types.SnapshotStatus.OK:
            if counter >= MAX_OPERATION_TIME:
                logger.error(
                    "Something went wrong with the snapshot! Process canceled")
                send_mail(
                    "<b>oVirt VM backup failed, with snapshot issues</b>")
                return None

            sleep(2)
            counter += 2
            # we need to try this, because after deleting a snapshot
            # this op will fail
            try:
                snap = snap_service.get()
            except sdk.NotFoundError:
                return None

        return snap
    else:
        return None


# remove the given snapshot
def delete_snapshot(snapshots_service, snap, vm_name, wait_for=True):
    snap_service = snapshots_service.snapshot_service(snap.id)

    logger.info("[{}] Removing the snapshot: '{}'...".format(
        vm_name, snap.description))
    snap_service.remove()

    # check after deleting again the snapshot
    # and wait for it, if it is necessary
    check_snapshot(snap, snapshots_service, wait_for)

    logger.info('[{}] Snapshot removing done!'.format(vm_name))


# create a snapshot from the given vm
def create_snapshot(vms_service, vm):
    logger.info('[{}] Create snapshot...'.format(vm.name))
    snapshots_service = vms_service.vm_service(vm.id).snapshots_service()

    # when there is an old snapshot,
    # we will delete them
    snap = check_snapshot(None, snapshots_service, True)

    if snap is not None:
        logger.info(
            '[{}] Old snapshot found, wait for deleting them...'.format(
                vm.name))

        delete_snapshot(snapshots_service, snap, vm.name, True)

    sleep(120)

    # create a new snapshot
    # fail creating a snapshot should not stop the script from working
    try:
        snap = snapshots_service.add(
          types.Snapshot(
            description='snapshot for backup',
            persist_memorystate=RAM_STATE,
          ),
        )
    except sdk.Error as oerr:
        logger.error(oerr)
        send_mail(oerr)

        return None

    # check the snapshot again
    check_snapshot(snap, snapshots_service, True)

    if snap is not None:
        logger.info('[{}] Creating snapshot done!'.format(vm.name))
        return snap
    else:
        logger.error('[{}] Creating snapshot failed!'.format(vm.name))
        return None


# clone the created snapshot in a new VM
def clone_snapshot_to_vm(vms_service, vm, snap):
    counter = 0
    snapshots_service = vms_service.vm_service(vm.id).snapshots_service()
    new_vm_name = '{}_{}_{}'.format(
        vm.name, VM_MIDDLE, datetime.now().strftime('%Y%m%d_%H%M%S'))

    # It can happen, that the snapshot state gives ok,
    # but the snapshot is still not free for cloning/deleting.
    # For this case we have to wait a bit more.
    sleep(120)

    check_snapshot(snap, snapshots_service, True)

    logger.info("[{}] Create VM clone from snapshot...".format(vm.name))

    try:
        vm_clone = vms_service.add(
            vm=types.Vm(
                name=new_vm_name,
                snapshots=[
                    types.Snapshot(
                        id=snap.id
                    )
                ],
                cluster=types.Cluster(
                    name=CLUSTER
                )
            )
        )
    except sdk.Error as oerr:
        logger.error(oerr)
        send_mail(oerr)

        return None

    cloned_vm_service = vms_service.vm_service(vm_clone.id)
    created = True

    # check if cloned vm is down,
    # what means that the cloning process has been completed
    while vm_clone.status != types.VmStatus.DOWN:
        if counter >= MAX_OPERATION_TIME:
            logger.error(
                "[{}] Creating VM clone from snapshot failed".format(vm.name))
            send_mail("Creating VM clone from snapshot failed!\n"
                      + "No backup for: <b>{}</b> at: <b>{}</b>".format(
                          vm.name, datetime.now().strftime('%H:%M:%S')))
            created = None
            break

        sleep(5)
        vm_clone = cloned_vm_service.get()
        counter + 5

    if created:
        logger.info(
            "[{}] Creating VM clone from snapshot completed!".format(vm.name))
        # is allways good to sleep a bit :)
        sleep(2)

        return vm_clone
    else:
        return None


# copy the cloned VM to the export domain
def export_vm_backup(system_service, vms_service, cloned_vm):
    cloned_vm_service = vms_service.vm_service(cloned_vm.id)
    sleep(4)

    logger.info("[{}] Export the VM clone...".format(cloned_vm.name))

    try:
        cloned_vm_service.export(
            exclusive=True,
            discard_snapshots=True,
            storage_domain=types.StorageDomain(
                name=EXPORT_DOMAIN
            )
        )
    except sdk.Error as oerr:
        logger.error(oerr)
        send_mail(oerr)

        return None

    exported_vm = None
    counter = 0

    # list all VMs in export domain and search for our exported VM
    # we don't want to run in a endless loop, for this we count the time
    # and when we go over the time this process will break
    while True:
        sleep(5)
        if counter >= MAX_OPERATION_TIME:
            break
        clone_service = cloned_vm_service.get()

        if clone_service.status == types.VmStatus.DOWN:
            exported_vm = True
            break

        counter += 5

    if exported_vm is None:
        logger.error("[{}] VM export failed!".format(cloned_vm.name))
        return None
    else:
        logger.info("[{}] VM export done!".format(cloned_vm.name))
        return exported_vm


# delete the temporary VM clone
def remove_vm(vms_service, cloned_vm):
    logger.info(
        '[{}] Delete cloned VM...'.format(cloned_vm.name))
    vm_service = vms_service.vm_service(cloned_vm.id)
    vm_service.remove()


# ------------------------------------------------------------------------------
# Run Main Program
# ------------------------------------------------------------------------------

def main():
    CheckConfigIntegrity(VM_LIST, CLUSTER, STORAGE_DOMAIN, EXPORT_DOMAIN)

    logger.info("Start backup process...")

    system_service = connection.system_service()
    vms_service = system_service.vms_service()

    for vm_name in VM_LIST:
        if not vms_service.list(search='name={}'.format(vm_name)):
            continue

        vm = vms_service.list(search='name={}'.format(vm_name))[0]

        check_old_backups(system_service, vms_service, vm)

        free_space_storage = get_storage_free_space(
            system_service, STORAGE_DOMAIN)
        free_space_export = get_storage_free_space(
            system_service, EXPORT_DOMAIN)

        # check size from vm and storage domains
        disks_size = get_vm_disks_size(vms_service, vm)

        # continue only if it is enough space
        if free_space_storage > disks_size < free_space_export:
            snap = create_snapshot(vms_service, vm)

            if snap is not None:
                cloned_vm = clone_snapshot_to_vm(vms_service, vm, snap)

            if cloned_vm is not None:
                snapshots_service = vms_service.vm_service(
                    vm.id).snapshots_service()
                # delete the snapshot asynchronous
                delete_snapshot(snapshots_service, snap, vm.name, False)

                export = export_vm_backup(
                    system_service, vms_service, cloned_vm)

            if export is not None:
                remove_vm(vms_service, cloned_vm)
        else:
            logger.error(
                "[{}] Not enough space for the backup!".format(vm.name))
            send_mail(
                "Not enough space for backup VM: <b>{}</b>!".format(vm.name))

    logger.info("Backup process done...\n" + 78 * "-")

    connection.close()


if __name__ == "__main__":
    main()
