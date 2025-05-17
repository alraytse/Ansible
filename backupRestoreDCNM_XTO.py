"""
.. module:: POAP
        :platform: Linux, Windows
        :synopsis: Reference module script to demonstrate DCNM REST APIs including POAP and autp-configuration.

.. moduleauthor:: Cisco DCNM team

.. note:: The configuration parameters need to be specified in :file:`ini.conf` file
                before running this script.

"""

import ConfigParser, sys, re, os, pty, stat
import json, datetime, shutil, tarfile
import requests


try:
    import xml.etree.cElementTree as et
except ImportError:
    import xml.etree.ElementTree as et

import logging
from logging import StreamHandler, FileHandler

import warnings
warnings.filterwarnings("ignore")

logger = logging.getLogger('dcnm_backup_restore')

requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS = 'DEFAULT:!DH:!ECDH:!aNULL'


# enums for backup options
# format: enum value in ini.conf, URI, file name
class Option:
    GeneralSetting = ['generalsetting',
                      { 'general': 'general?detail=True',
                'poap': 'poap?detail=True',
                'dci': 'dci?detail=True',
                'vxlan': 'vxlan?detail=True',
                'mobility-domains': 'mobility-domains?detail=True',
                'segmentid-ranges': 'segmentid-ranges?detail=True'
                },
                'general_settings' ]
    LanFabric = ['lanfabric', 'fabrics?detail=True', 'lan_fabrics']
    NavGroup = ['navgroup', 'navGroups', 'nav_groups']
    AdminSetting = [ 'adminSetting',
                    {  'switchGroup': 'switchGroups',
                        'portGroup': 'portGroups',
                        'dataSourceCompute': 'computeDataSource'
                    },
                    'admin_settings' ]

class POAPOption:
    SettingFile = ['Setting', '/poap/settings/', 'poap_settings']
    Definition = ['Definition', '/poap/definitions/', 'poap_definitions']
    AllTemplate = ['AllTemplate', '/poap/templates/', 'poap_templates']
    DHCPScope = ['DHCPScope', '/poap/dhcp/scopes/', 'poap_dhcp_scope']
    ImageConfigServer = ['ImageConfigServer', '/poap/servers/', 'poap_image_config_server']

class AutoConfigOption:
    OrgPartitionNetwork = ['Network',
                           {'org': '/organizations?detail=True',
                            'partition': '/partitions?detail=True',
                            'network' : '/networks?detail=True'},
                           'auto_config_networks' ]
    BorderLeafPair = [ 'BorderLeafPair', 'dci/paired-devices?detail=True', 'auto_config_border_leaf_pairs' ]
    ExtendedPartition = [ 'ExtendedPartition', 'dci/extended-partitions', 'auto_config_extended_partitions' ]
    EndHost = [ 'EndHost', 'end-hosts?detail=True', 'auto_config_end_hosts' ]
    CustomizedProfile = [ 'Profile', 'profiles', 'auto_config_profiles' ]

def enum(*named_values):
    return type('Enum', (), dict(zip(named_values, named_values)))

class DCNMClient():
    """ This DCNM client class interacts with DCNM via DCNM REST API to populate
    organization (tenant), partition (vrf, VDC) and network data.
    """

    def __init__(self, dcnm_params, folder, option_params, override_options=None):
        """Create a new instance of DCNM client.

        :param dict params_dcnm: DCNM configuration parameters, e.g. DCNM server IP, user name.

        """

        self._access_info = self._extract_access_info(dcnm_params)
        (self._ip, self._user, self._pwd, self._http_https) = self._access_info
        if (not self._ip) or (not self._user) or (not self._pwd) or (not option_params):
            raise ValueError, '[DCNMClient] Input DCNM IP, user name or password parameter, or backup option is not ' \
                              'specified'
        logger.info('[DCNMClient] DCNM IP: %s, User: %s.' % (self._ip, self._user))

        # save the input fabric names
        self._fabric_names = dcnm_params.get('fabricnames')

        self._option_params = None
        self._override_options = None

        # save the input parameters
        self.set_options(option_params)
        self.set_override_options(override_options)
        self._dest_folder = folder;

        # read the nav groups
        self._nav_groups = self._read_nav_groups()
        self._restored_nav_groups = {}

        # url timeout: 10 seconds
        self._TIMEOUT_RESPONSE = 10

        self._BACKUP_CATEGORY = 'backupCategory'
        self._LDAP_CERTIFICATE_FOLDER = '/etc/openldap/certs'
        self._REPOSITORY_FOLDER = '/var/lib/dcnm'
        self._LICENSE_FOLDER = '/usr/local/cisco/dcm/licenses'
        self._REPORT_FOLDER = '/usr/local/cisco/dcm/fm/reports'
        self._backup_folders = [ self._LDAP_CERTIFICATE_FOLDER,
                                 self._LICENSE_FOLDER]

        self._summary_file = self._compose_summary_file()

        # create SSH client
        self._ssh_client = SSHClient(self._ip, 'root', self._pwd)
        standby_ip = dcnm_params.get('standby')
        self._ssh_client_standby = SSHClient(standby_ip, 'root', self._pwd) if standby_ip else None
        self._STANDBY_SUBFOLDER = 'standby'

        self._STATUS_CODES = enum('Success', 'Failure', 'InvalidRequest', 'AlreadyExists', 'ConnectionError',
                                  'HttpError', 'TimeoutError', 'ValueError')

        self._PAYLOAD_CONTENT_TYPE = enum('JSON', 'Plain', 'Form')

    def set_options(self, option_params):
        self._option_params = option_params

    def set_override_options(self, override_options):
        self._override_options = override_options

    def backup_folder(self):
        self._write_to_summary()
        for folder in self._backup_folders:
            for x in range(2):
                absolute_folder = self._dest_folder + folder
                ssh_client = self._ssh_client
                if x == 1:
                    if self._ssh_client_standby:
                        ssh_client = self._ssh_client_standby
                        absolute_folder = '%s/%s/%s' % (self._dest_folder, self._STANDBY_SUBFOLDER, folder)
                    else:
                        continue
                parent_folder = absolute_folder.rpartition('/')[0]
                os.makedirs(parent_folder)
                ssh_client.pop(folder, parent_folder)
        self._write_to_summary('Backed up folders: %s' % (self._backup_folders))

    def backup_general_setting(self):
        self._write_to_summary()
        if not self._is_option_set(Option.GeneralSetting[0]):
            self._write_to_summary('Skip general setting backup as its flag is not set in configuration file.')
            return

        prefix = '%s://%s/rest/settings/' % (self._http_https, self._ip)

        logger.info('Backing up auto-config settings ....')
        # general setting
        file_name = self._compose_file_name(Option.GeneralSetting[2])
        self._write_init(file_name)
        types = ['general', 'poap', 'dci', 'vxlan', 'mobility-domains', 'segmentid-ranges']

        for type in types:
            setting_url = prefix + type + '?detail=True'
            (status, setting) = self._send_request('GET', setting_url)
            if not setting:
                continue

            url = Option.GeneralSetting[1].get(type)
            size = len(setting) if isinstance(setting, list) else 1
            separator = self._compose_separator(url, size=size)
            self._write_to_file(file_name, setting, separator)
        self._write_to_summary('Backed up auto-config settings for %s' % (types))

    # return a list of LAN fabric names including Default_LAN
    def backup_lan_fabrics(self):
        self._write_to_summary()
        if not self._is_option_set(Option.LanFabric[0]):
            self._write_to_summary('Skip LAN fabric backup as its flag is not set in configuration file.')
            return

        # loop thru LAN fabrics
        logger.info('Backing up LAN fabrics ....')
        file_name = self._compose_file_name(Option.LanFabric[2])
        self._write_init(file_name)
        if not self._is_dcnm_10():
            return

        fabric_names = ['Default_LAN']
        fabrics_url = '%s://%s/rest/fabrics?detail=True' % (self._http_https, self._ip)
        (status, fabrics) = self._send_request('GET', fabrics_url)
        if fabrics:
            url = Option.LanFabric[1]
            separator = self._compose_separator(url, size=len(fabrics))
            self._write_to_file(file_name, fabrics, separator)
        self._write_to_summary('Backed up LAN fabrics: %s' % (fabric_names))
        return fabric_names

    def backup_nav_groups(self):
        self._write_to_summary()
        logger.info('Backing up navigation groups ....')
        file_name = self._compose_file_name(Option.NavGroup[2])
        self._write_init(file_name)
        if not self._is_dcnm_10():
            return

        url = '%s://%s/fm/fmrest/device-group/groups/' % (self._http_https, self._ip)
        (status, groups) = self._send_request('GET', url, None, 'Get navigation groups')
        if groups:
            url = Option.NavGroup[1]
            separator = self._compose_separator(url, size=len(groups))
            self._write_to_file(file_name, groups, separator)
        self._write_to_summary('Backed up navigation groups')

    def backup_admin_setting(self):
        # nav group (switch group)
        #self.backup_nav_groups()

        self._write_to_summary()
        logger.info('Backing up admin setings, including customized port group, compute data source ....')
        file_name = self._compose_file_name(Option.AdminSetting[2])
        self._write_init(file_name)

        # switch group
        self._backup_admin_switch_group(file_name)
        # customized port group
        self._backup_admin_port_group(file_name)

        # data source (compute)
        self._backup_admin_compute_data_source(file_name)


    def backup_auto_config_data(self):
        self._write_to_summary()
        self._write_to_summary()
        fabric_names = self._get_fabric_names()
        logger.info('Start of auto-config data backup. Merged LAN fabrics: %s' % fabric_names)

        # backup customized profile first, in case those profiles are used by partition and network
        self._backup_auto_config_profile()

        # backup border leaf pairing, so that partitions can be extended
        self._backup_auto_config_bl_pair(fabric_names)

        self._backup_auto_config_org_partition_network(fabric_names)
        self._backup_auto_config_end_host(fabric_names)
        self._backup_auto_config_extended_partitions(fabric_names)
        self._write_to_summary('End of auto-config data backup.')

    def backup_poap_data(self):
        self._write_to_summary()
        self._write_to_summary()
        fabric_names = self._get_fabric_names()
        logger.info('Start of POAP data backup. Merged LAN fabrics: %s' % fabric_names)

        self._backup_poap_dhcp_scope()
        self._backup_poap_image_config_server()
        self._backup_poap_all_templates()
        self._backup_poap_setting_file(fabric_names)
        self._backup_poap_definition(fabric_names)

        self._write_to_summary('End of POAP data backup.')

    def restore_folder(self):
        self._write_to_summary('Start of folder restore.')
        for folder in self._backup_folders:
            for x in range(2):
                absolute_folder = self._dest_folder + folder
                ssh_client = self._ssh_client
                if x == 1:
                    if self._ssh_client_standby:
                        absolute_folder = '%s/%s/%s' % (self._dest_folder, self._STANDBY_SUBFOLDER, folder)
                        ssh_client = self._ssh_client_standby
                    else:
                        continue
                target_folder = folder
                parent_folder = folder.rpartition('/')[0]
                if folder.endswith('/'):
                    target_folder = folder[:-1]
                mv_cli = 'mv ' + folder + ' ' + target_folder + '_orig'
                mkdir_cli = 'mkdir -p ' + parent_folder
                ssh_client.cmd(mv_cli)
                ssh_client.cmd(mkdir_cli)
                ssh_client.push(absolute_folder, parent_folder)
                if folder == self._LDAP_CERTIFICATE_FOLDER:
                    chmod_cli = 'chown ldap:ldap %s/*.pem' % (folder)
                    ssh_client.cmd(chmod_cli)
        self._write_to_summary("Restored folders: %s " % (self._backup_folders))
        self._write_to_summary('End of folder restore.')

    def restore_admin_setting(self):
        self._write_to_summary()
        self._write_to_summary()
        self._write_to_summary('Start of admin setting restore.')

        # restore customized profile first, in case those profiles are used by partition and network
        self._restore_admin_switch_group()
        self._restore_admin_port_group()
        self._restore_admin_compute_data_source()

        self._write_to_summary('End of admin setting restore.')

    def restore_auto_config_data(self):
        self._write_to_summary()
        self._write_to_summary()
        fabric_names = self._get_fabric_names()
        self._write_to_summary('Start of auto-config data restore. Merged LAN fabrics: %s' % (fabric_names))

        # restore customized profile first, in case those profiles are used by partition and network
        self._restore_auto_config_profile()

        # restore border leaf pairing, so that partitions can be extended
        self._restore_auto_config_bl_pair(fabric_names)

        self._restore_auto_config_org_partition_network(fabric_names)
        self._restore_auto_config_end_host(fabric_names)

        self._restore_auto_config_extended_partitions(fabric_names)
        self._write_to_summary('End of auto-config data restore.')

    def restore_poap_data(self):
        self._write_to_summary()
        self._write_to_summary()
        fabric_names = self._get_fabric_names()
        self._write_to_summary('Start of POAP data restore. Merged LAN fabrics: %s' % (fabric_names))

        self._restore_poap_dhcp_scope()
        self._restore_poap_image_config_server()
        # restore POAP template first
        self._restore_poap_template()
        self._restore_poap_setting_file(fabric_names)
        self._restore_poap_definition(fabric_names)

        self._write_to_summary('End of POAP data restore.')

    def restore_general_setting(self):
        if not self._is_option_set(Option.GeneralSetting[0]):
            logger.info('Skip the general setting restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring auto-config general setting ....')
        file_name = self._compose_file_name(Option.GeneralSetting[2])

        dest_prefix = '%s://%s/rest/settings/' % (self._http_https, self._ip)
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if category:
                type = category.split('?')[0]
                if self._is_matched_url(category, Option.GeneralSetting[1].get('poap')):
                # POAP setting response may not contain value field for name
                    for entry in data:
                        if not entry.get('value'):
                            entry.update({'value': ''})
                elif self._is_matched_url(category, Option.GeneralSetting[1].get('general')):
                    if self._override_options and self._override_options.get('fabricOverride'):
                        fabric_override = self._override_options.get('fabricOverride')
                        eth1_ip = fabric_override.get('eth1ip')
                        peer_eth1_ip = fabric_override.get('peereth1ip')
                        fqdn = fabric_override.get('fqdn')
                        if eth1_ip:
                            data['ldapServer'] = eth1_ip
                            data['amqpServer'] = eth1_ip
                            data['ldapPassword'] = self._pwd
                            data['amqpPassword'] = self._pwd
                        if peer_eth1_ip:
                            data['ldapPeerServer'] = peer_eth1_ip
                            data['ldapPeerPassWord'] = self._pwd
                        if fqdn:
                            data['xmppServer'] = fqdn
                            data['xmppPassword'] = self._pwd

                dest_url = dest_prefix + type
                (status, dest_setting) = self._send_request('GET', dest_url)
                action = 'PUT' if dest_setting else 'POST'
                if self._is_matched_url(category, Option.GeneralSetting[1].get('mobility-domains'))\
                        or self._is_matched_url(category, Option.GeneralSetting[1].get('segmentid-ranges')):
                    for tmp in data:
                        key = 'mobilityDomainName' if self._is_matched_url(category, Option.GeneralSetting[1].get('mobility-domains')) else 'orchestratorId'
                        value = tmp.get(key)
                        if dest_setting and value and self._is_existing_key_value(dest_setting, key, value):
                             dest_url = dest_prefix + type + '/' + value
                        (status, res) = self._send_request(action, dest_url, tmp)
                else:
                    (status, res) = self._send_request(action, dest_url, data)
                statuses.update({status: statuses.get(status, 0) + 1})

                size = len(data) if isinstance(data, list) else 1
                logger.info('Total count of restored general setting: backed-up: %s, restored: (%s)' % (size,
                                                                                       self._compose_status_summary(
                                                                                           statuses)))

        logger.info('Restored auto-config general settings')

    # return a list of LAN fabric names including Default_LAN
    def restore_lan_fabrics(self):
        if not self._is_option_set(Option.LanFabric[0]):
            logger.info('Skip LAN fabric restore as its flag is not set in configuration file.')
            return

        # loop thru LAN fabrics
        logger.info('Restoring LAN fabrics ....')
        file_name = self._compose_file_name(Option.LanFabric[2])

        fabric_names = ['Default_LAN']
        prefix = '%s://%s/rest/' % (self._http_https, self._ip)
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, Option.LanFabric[1]):
                fabrics_url = prefix + 'fabrics'
                for fabric in data:
                    self._send_request('POST', fabrics_url, fabric)
                    fabric_name = fabric.get('name')
                    logger.info('Restored LAN fabric: %s.' % (fabric_name))
                    fabric_names.append(fabric_name)
        return fabric_names

    def _read_nav_groups(self):
        file_name = self._compose_file_name(Option.NavGroup[2])
        if not os.path.isfile(file_name):
            return {}

        logger.info('Restoring navigation groups ....')
        group_name_ids = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, Option.NavGroup[1]):
                groups = data.get('row') if data.get('row') else data.get('rows')
                for group in groups:
                    entry = group.get('entry')
                    if entry:
                        group_name_ids.update({entry[0]: entry[1]})
        return group_name_ids

    def _backup_admin_switch_group(self, file_name):
        if self._is_dcnm_10():
            self.backup_nav_groups()
        else:
            url = Option.AdminSetting[1].get('switchGroup')
            switch_groups_info = []
            # SOAP request
            switch_group_url = '%s://%s/DbAdminWSService/DbAdminWS' % (self._http_https, self._ip)
            switch_groups_request_body = '<tns:getGroupNavigation/>'
            res_soap = self._send_request_soap(switch_group_url, switch_groups_request_body)
            if res_soap:
                doc = et.fromstring(res_soap)
                res_txt = doc.find('.//result').text
                res_xml = et.fromstring(res_txt)
                switch_groups_info = {}
                self._extract_group_members(res_xml, switch_groups_info)

                separator = self._compose_separator(url)
                self._write_to_file(file_name, switch_groups_info, separator)
                self._write_to_summary('Total count of backed-up switch groups: %s\n' % len(switch_groups_info))

        self._write_to_summary('Backed up switch groups')

    def _extract_group_members(self, xml_doc, group_members_info={}):
        if not xml_doc:
            return

        branches = xml_doc.findall("./group[@isBranch='true']")
        if branches:
            for branch in branches:
                branch_name = branch.get('name')
                branch_members = {}
                leaf_members = branch.findall("./groupMember[@isBranch='false']")
                leaf_member_names = [ x.get('name') for x in leaf_members ]
                if leaf_member_names:
                    branch_members.update({'leaf': leaf_member_names})
                group_members_info.update({ branch_name: branch_members})
                self._extract_group_members(branch, branch_members)

    def _backup_admin_port_group(self, file_name):
        url = Option.AdminSetting[1].get('portGroup')
        if not self._is_dcnm_10():
            # SOAP request
            port_group_url = '%s://%s/DbInventoryWSService/DbInventoryWS' % (self._http_https, self._ip)
            port_groups_request_body = '<tns:getAllAppGroups/>'
            port_groups_info = self._extract_soap_content(port_group_url, port_groups_request_body)

            if port_groups_info:
                separator = self._compose_separator(url)
                self._write_to_file(file_name, port_groups_info, separator)
                # individual port group info
                for port_group in port_groups_info:
                    # SOAP request
                    port_group_request_body = '<tns:getPortGroupMember><arg0>%s</arg0></tns:getPortGroupMember>' % (
                        port_group.get('id'))
                    port_group_info = self._extract_soap_content(port_group_url, port_group_request_body)
                    if port_group_info is not None:
                        separator = self._compose_separator(url, portGroupName=port_group.get('name'))
                        self._write_to_file(file_name, port_group_info, separator)
                self._write_to_summary('Total count of backed-up customized port groups: %s\n' % len(port_groups_info))

        # TODO: REST for DCNM 10+

        self._write_to_summary('Backed up customized port groups')

    def _backup_admin_compute_data_source(self, file_name):
        url = Option.AdminSetting[1].get('dataSourceCompute')
        if not self._is_dcnm_10():
            port_groups_info = []
            # SOAP request
            data_source_url = '%s://%s/SanWSService/SanWS' % (self._http_https, self._ip)
            data_sources_request_body = '<tns:getVirtualCenters/>'
            data_sources_info = self._extract_soap_content(data_source_url, data_sources_request_body)

            if data_sources_info is not None:
                separator = self._compose_separator(url)
                self._write_to_file(file_name, data_sources_info, separator)
                self._write_to_summary('Total count of backed-up compute data sources: %s\n' % len(data_sources_info))

        # TODO: REST for DCNM 10+

        self._write_to_summary('Backed up compute data sources')

    def _backup_auto_config_org_partition_network(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.OrgPartitionNetwork[0]):
            self._write_to_summary('Skip auto-config orgization/partition/network backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Backing up auto-config organization/partition/network ....')
        file_name = self._compose_file_name(AutoConfigOption.OrgPartitionNetwork[2])
        self._write_init(file_name)
        # get all the organizations
        for fabric_name in fabric_names:
            prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
            orgs_url = prefix + 'organizations?detail=True'
            (status, orgs) = self._send_request('GET', orgs_url)
            if not orgs:
                continue
            url =  AutoConfigOption.OrgPartitionNetwork[1].get('org')
            separator = self._compose_separator(url, fabricName=fabric_name, size=len(orgs))
            self._write_to_file(file_name, orgs, separator)
            names = []
            for org in orgs:
                # create org in destination dcnm
                org_name = org['organizationName']
                logger.info('Backing up organization %s ....' % org_name)
                partitions_url = '%sorganizations/%s/partitions?detail=True' % (prefix, org_name)
                (status, partitions) = self._send_request('GET', partitions_url)
                if not partitions:
                    continue

                url =  AutoConfigOption.OrgPartitionNetwork[1].get('partition')
                separator = self._compose_separator(url, fabricName=fabric_name, orgName=org_name,
                                                    size=len(partitions))
                self._write_to_file(file_name, partitions, separator)
                for partition in partitions:
                    # create partition in destination dcnm
                    partition_name = partition['partitionName']

                    logger.info('Backing up partition %s under organization %s ....' % (partition_name, org_name))
                    # get all the network details incl. DHCP scopes
                    networks_url = '%sorganizations/%s/partitions/%s/networks?detail=True' % (prefix, org_name, partition_name)
                    (status, networks) = self._send_request('GET', networks_url)
                    url =  AutoConfigOption.OrgPartitionNetwork[1].get('network')
                    separator = self._compose_separator(url, fabricName=fabric_name, orgName=org_name,
                                                        partitionName=partition_name, size=len(networks))
                    self._write_to_file(file_name, networks, separator)
                    names = [ x.get('networkName') for x in networks ]
                    self._write_to_summary('Backed up auto-config networks for partition %s of organization %s under '
                                       'LAN fabric %s: \n%s ' % (partition_name, org_name, fabric_name, names))
                    self._write_to_summary('Total count of backed-up auto-config networks for partition %s '
                                       'of organization %s under LAN fabric %s: %s\n' % (partition_name, org_name,
                                                                                        fabric_name, len(names)))

                names = [ x.get('partitionName') for x in partitions]
                self._write_to_summary('Backed up auto-config partitions for organization %s under '
                                   'LAN fabric %s: \n%s ' % (org_name, fabric_name, names))
                self._write_to_summary('Total count of backed-up auto-config partitions '
                                       'for organization %s under LAN fabric %s: %s\n' % (org_name,
                                                                                        fabric_name, len(names)))

            names = [ x.get('organizationName') for x in orgs]
            self._write_to_summary('Backed up auto-config organization under '
                               'LAN fabric %s: \n%s ' % (fabric_name, names))
            self._write_to_summary('Total count of backed-up auto-config '
                                   'organization under LAN fabric %s: %s\n' % (fabric_name, len(names)))

        self._write_to_summary('Backed up auto-config organization/partition/network')

    def _backup_auto_config_profile(self):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.CustomizedProfile[0]):
            self._write_to_summary('Skip auto-config customized profile backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Backing up auto-config customized profiles ....')
        file_name = self._compose_file_name(AutoConfigOption.CustomizedProfile[2])
        self._write_init(file_name)
        # get all the profiles
        prefix = '%s://%s/rest/auto-config/' % (self._http_https, self._ip)
        profiles_url = prefix + 'profiles?detail=True'
        (status, profiles) = self._send_request('GET', profiles_url)
        if not profiles:
            return

        url =  AutoConfigOption.CustomizedProfile[1]
        separator = self._compose_separator(url, size=len(profiles))
        self._write_to_file(file_name, profiles, separator, True)
        names = [ x.get('profileName') for x in profiles]
        self._write_to_summary('Backed up auto-config profiles: \n%s ' % (names))
        self._write_to_summary('Total count of backed-up auto-config profiles: %s\n' % (len(names)))
        self._write_to_summary('Backed up auto-config profiles')

    def _backup_auto_config_end_host(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.EndHost[0]):
            self._write_to_summary('Skip auto-config end host backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Backing up auto-config end hosts ....')
        file_name = self._compose_file_name(AutoConfigOption.EndHost[2])
        self._write_init(file_name)
        # get all the end host
        for fabric_name in fabric_names:
            prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(
                fabric_name))
            end_hosts_url = prefix + 'end-hosts?detail=True'
            (status, end_hosts) = self._send_request('GET', end_hosts_url)
            if not end_hosts:
                continue
            url = AutoConfigOption.EndHost[1]
            separator = self._compose_separator(url, fabricName=fabric_name, size=len(end_hosts))
            self._write_to_file(file_name, end_hosts, separator)
            logger.info('Backed up auto-config end hosts for LAN fabric %s' % (fabric_name))
            names = [ x.get('name') for x in end_hosts]
            self._write_to_summary('Backed up auto-config end-hosts under '
                               'LAN fabric %s: \n%s ' % (fabric_name, names))
            self._write_to_summary('Total count of backed-up '
                                   'end-hosts under LAN fabric %s: %s\n' % (fabric_name, len(names)))

    def _backup_auto_config_bl_pair(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.BorderLeafPair[0]):
            self._write_to_summary('Skip auto-config Border Leaf pairing backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Backing up auto-config Border Leaf pairing ....')
        file_name = self._compose_file_name(AutoConfigOption.BorderLeafPair[2])
        self._write_init(file_name)
        # get all the organizations
        for fabric_name in fabric_names:
            prefix = '%s://%s/rest/auto-config/%sdci/' % (self._http_https, self._ip, self._compose_fabric_prefix(
                fabric_name))
            bl_url = prefix + 'paired-devices?detail=True'
            (status, bls) = self._send_request('GET', bl_url)
            if not bls:
                continue
            url = AutoConfigOption.BorderLeafPair[1]
            separator = self._compose_separator(url, fabricName=fabric_name, size=len(bls))
            self._write_to_file(file_name, bls, separator)
            names = []
            for bl in bls:
                peers = bl.get('dciPeers')
                if peers:
                    for peer in peers:
                        names.append((bl.get('name'), peer.get('name')))
                else:
                    names.append(bl.get('name'))
            self._write_to_summary('Backed up auto-config Border Leaf pairing for '
                               'LAN fabric %s: \n%s ' % (fabric_name, names))
            self._write_to_summary('Total count of backed-up '
                                   'Border Leaf pairing for LAN fabric %s: %s\n' % (fabric_name, len(names)))
        self._write_to_summary('Backed up Border Leaf pairing')

    def _backup_auto_config_extended_partitions(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.ExtendedPartition[0]):
            self._write_to_summary('Skip auto-config extended partition backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Backing up auto-config extended partitions ....')
        file_name = self._compose_file_name(AutoConfigOption.ExtendedPartition[2])
        self._write_init(file_name)
        # call ldapsearch to retrieve all the extended partitions
        cli = '/usr/bin/ldapsearch -x -D "cn=admin,dc=cisco,dc=com" -b "dc=cisco, dc=com"  "(objectclass=bl-dci)" -h ' \
              '' + \
              self._ip + ' -w ' + self._pwd + ' > ' + file_name
        os.system(cli)
        self._write_to_summary('Backed up extended partitions')

    def _backup_poap_dhcp_scope(self):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.DHCPScope[0]):
            self._write_to_summary('Skip POAP DHCP scope backup as its flag is not set in configuration file.')
            return

        logger.info('Backing up POAP DHCP scopes ....')
        file_name = self._compose_file_name(POAPOption.DHCPScope[2])
        self._write_init(file_name)
        prefix = '%s://%s/rest/poap/' % (self._http_https, self._ip)
        scopes_url = prefix + 'dhcp/scopes?detail=True'
        (status, scopes) = self._send_request('GET', scopes_url)
        if scopes:
            url = POAPOption.DHCPScope[1]
            separator = self._compose_separator(url, size=len(scopes))
            self._write_to_file(file_name, scopes, separator)
            names = [ x.get('scopeName') for x in scopes ]
            self._write_to_summary('Backed up POAP DHCP scopes: \n%s' % (names))
            self._write_to_summary('The total count of backed up POAP DHCP scopes: %s\n' % (len(names)))
        self._write_to_summary('Backed up POAP DHCP scopes')

    def _backup_poap_image_config_server(self):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.ImageConfigServer[0]):
            self._write_to_summary('Skip POAP image and config servers backup as its flag is not set in configuration '
                                   'file.')
            return

        logger.info('Backing up POAP image and config servers ....')
        file_name = self._compose_file_name(POAPOption.ImageConfigServer[2])
        self._write_init(file_name)
        prefix = '%s://%s/rest/poap/' % (self._http_https, self._ip)
        servers_url = prefix + 'servers/'
        (status, servers) = self._send_request('GET', servers_url)
        if servers:
            url = POAPOption.ImageConfigServer[1]
            separator = self._compose_separator(url, size=len(servers))
            self._write_to_file(file_name, servers, separator)
        names = [ x.get('serverName') for x in servers ] if servers else []
        self._write_to_summary('Backed up POAP image and config server: \n%s ' % (names))
        self._write_to_summary('Total count of backed-up POAP image and config servers: %s\n' % (len(names)))
        self._write_to_summary('Backed up POAP image and config servers')

    def _backup_poap_setting_file(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.SettingFile[0]):
            self._write_to_summary('Skip POAP setting file backup as its flag is not set in configuration file.')
            return

        logger.info('Backing up POAP setting files ....')
        file_name = self._compose_file_name(POAPOption.SettingFile[2])
        self._write_init(file_name)
        for fabric_name in fabric_names:
            prefix = '%s://%s/rest/poap/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
            file_url = prefix + 'settings/'
            (status, setting_files) = self._send_request('GET', file_url)
            if not setting_files:
                continue
            url = POAPOption.SettingFile[1]
            separator = self._compose_separator(url, fabricName=fabric_name, size=len(setting_files))
            self._write_to_file(file_name, setting_files, separator)
            names = [ x.get('templateSettingsName') for x in setting_files ]
            self._write_to_summary('Backed up POAP setting files for LAN fabric %s: \n%s ' % (fabric_name, names))
            self._write_to_summary('Total count of backed-up POAP setting files for LAN fabric %s: %s\n' % (
                fabric_name, len(names)))
        self._write_to_summary('Backed up POAP setting files')

    # return list of POAP templates used
    def _backup_poap_definition(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.Definition[0]):
            self._write_to_summary('Skip POAP definition backup as its flag is not set in configuration file.')
            return

        template_names = set()
        logger.info('Backing up POAP definition data ....')
        file_name = self._compose_file_name(POAPOption.Definition[2])
        self._write_init(file_name)
        # get all the organizations
        for fabric_name in fabric_names:
            fabric_name = fabric_name.strip()
            prefix = '%s://%s/rest/poap/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
            defs_url = prefix + 'switch-definitions'
            (status, defs) = self._send_request('GET', defs_url)
            switch_names = []
            if not defs:
                continue
            for def_data in defs:
                # create POAP definition in destination dcnm
                switch_name = def_data.get('switchName')
                serial_number = def_data.get('serialNumber')
                logger.info('Backing up POAP definitions for switch %s (%s) under LAN fabric %s ....' % (switch_name,
                                                                                             serial_number, fabric_name))
                def_url = defs_url + '/' + serial_number
                (status, def_detail) = self._send_request('GET', def_url, None, 'Get POAP definition')
                if not def_detail:
                    continue

                nav_group = def_data.get('lanGroup')
                def_fabric_name = fabric_name
                if self._nav_groups and self._nav_groups.get(nav_group):
                    def_fabric_name = self._nav_groups.get(nav_group)
                url = POAPOption.Definition[1]
                separator = self._compose_separator(url, switchName=switch_name, serialNumber=serial_number,
                                                    fabricName=def_fabric_name)
                self._write_to_file(file_name, def_detail, separator)
                if def_detail and def_detail.get('templateName'):
                    template_names.add(def_detail.get('templateName'))
                switch_names.append(switch_name)
            self._write_to_summary('Backed up POAP definitions for LAN fabric %s: \n%s' % (fabric_name, switch_names))
            self._write_to_summary('Total count of backed up POAP definitions for LAN fabric %s: %s\n' % (
                    fabric_name, len(switch_names)))
        self._write_to_summary('Backed up POAP definitions')

        if not self._is_option_set('poaps', POAPOption.AllTemplate[0]):
            logger.info('Backing up POAP templates used in POAP definitions ...')
            self._backup_poap_templates(template_names)

    def _backup_poap_all_templates(self):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.AllTemplate[0]):
            self._write_to_summary('Skip POAP all templates backup as its flag is not set in configuration file.')
            return

        if not self._is_dcnm_10():
            self._write_to_summary('Backed up all the POAP templates')
            return self._backup_poap_templates_soap(None)

        template_names = set()
        logger.info('Backing up POAP all templates data ....')
        file_name = self._compose_file_name(POAPOption.AllTemplate[2])
        self._write_init(file_name)
        # get the list of POAP template names
        prefix = '%s://%s/fm/fmrest/' % (self._http_https, self._ip)
        templates_url = prefix + 'config/templates?tempalteType=POAP'
        (status, templates) = self._send_request('GET', templates_url)
        template_names = [ template.get('name') for template in templates ] if templates else None
        return self._backup_poap_templates(template_names)

    def _backup_poap_templates(self, template_names):
        self._write_to_summary()
        if not template_names:
            self._write_to_summary('No template names are specified.')
            return

        if not self._is_dcnm_10():
            self._write_to_summary('Backed up POAP templates: \n%s' % (template_names))
            self._backup_poap_templates_soap(template_names)
            self._write_to_summary('The total count of backed up POAP templates: %s' % (len(template_names)))
            self._write_to_summary('Backed up POAP templates')
            return

        logger.info('Backing up POAP template data ....')
        file_name = self._compose_file_name(POAPOption.AllTemplate[2])
        self._write_init(file_name)
        for template_name in template_names:
            if not template_name:
                continue
            prefix = '%s://%s/fm/fmrest/' % (self._http_https, self._ip)
            logger.info('Backing up POAP template %s ....' % (template_name))
            template_url = prefix + 'config/templates/' + template_name
            (status, template) = self._send_request('GET', template_url, 'Get POAP template')
            if not template:
                continue

            url = POAPOption.AllTemplate[1]
            separator = self._compose_separator(url, templateName=template_name)
            self._write_to_file(file_name, template, separator)

        self._write_to_summary('Backed up POAP templates: \n%s' % (template_names))
        self._write_to_summary('The total count of backed up POAP templates: %s\n' % (len(template_names)))
        self._write_to_summary('Backed up POAP templates')

    def _backup_poap_templates_soap(self, template_names):
        #if not template_names:
        #    logger.info('No template names are specified.')
        #    return

        logger.info('Backing up POAP template data via SOAP ....')
        file_name = self._compose_file_name(POAPOption.AllTemplate[2])
        self._write_init(file_name)

        # get all the POAP templates
        poap_templates_url = '%s://%s/ConfigTemplateWSService/ConfigTemplateWS' % (self._http_https, self._ip)
        poap_templates_body = '<tns:getAllPoapTemplates/>'
        templates_soap = self._send_request_soap(poap_templates_url, poap_templates_body)

        templates_info = []
        key_instance_id = 'instanceClassId'
        key_instance_name = 'instanceName'
        if templates_soap:
            doc = et.fromstring(templates_soap)
            for item in doc.findall('.//item'):
                template_name = item.findtext('name')
                instance_id = item.findtext(key_instance_id)
                instance_name = item.findtext(key_instance_name)
                if template_name and instance_id and instance_name:
                    if (template_names and template_name in template_names) or (not template_names):
                        templates_info.append({'name': template_name, key_instance_id: instance_id, key_instance_name:
                        instance_name})

        # get template contents
        url = POAPOption.AllTemplate[1]
        for info in templates_info:
            instance_id = info.get(key_instance_id)
            instance_name = info.get(key_instance_name)
            template_name = info.get('name')
            poap_template_content_body = '<tns:getTemplateContents><arg0><item><%s>%s</%s><%s>%s</%s></item></arg0' \
                                         '></tns:getTemplateContents>' % (key_instance_id, instance_id,
                                                                          key_instance_id, key_instance_name,
                                                                          instance_name, key_instance_name)
            template_content_soap = self._send_request_soap(poap_templates_url, poap_template_content_body)
            doc = et.fromstring(template_content_soap)
            template_content = doc.find('.//item')
            if template_content is not None:
                # compose template
                template = { 'templateName': template_name, 'content': template_content.text }
                separator = self._compose_separator(url, templateName=template_name)
                self._write_to_file(file_name, template, separator)
        logger.info('Backed up POAP templates via SOAP')

    def _restore_admin_compute_data_source(self):
        self._write_to_summary()
        logger.info('Restoring Admin data source (compute) ....')
        file_name = self._compose_file_name(Option.AdminSetting[2])
        url = '%s://%s/fm/fmrest/san/addVirtualCenter' % (self._http_https, self._ip)
        ips = []
        for (category_line, data) in self._read_from_file(file_name):
            category = category_line.get(self._BACKUP_CATEGORY)
            statuses = {}
            if category:
                if self._is_matched_url(category, Option.AdminSetting[1].get('dataSourceCompute')):
                    for entry in data:
                        ip = entry.get('ip')
                        payload = { 'vcIP': ip, 'username': entry.get('userName'), 'serverIpaddress':
                            self._ip}
                        (status, res) = self._send_request('POST', url, payload,
                                                       content_type=self._PAYLOAD_CONTENT_TYPE.Form)
                        statuses.update({status: statuses.get(status, 0) + 1})
                        ips.append(ip)
                        self._write_to_summary('Restored Admin compute data sources: \n%s' % (ip))
        self._write_to_summary('Total count of restored Admin compute data sources: backed-up: %s, '
                           'restored: (%s)\n' % (len(ips), self._compose_status_summary(statuses)))

    def _restore_admin_switch_group(self):
        self._write_to_summary()
        logger.info('Restoring Admin switch groups ....')
        file_name = self._compose_file_name(Option.AdminSetting[2])
        switch_name_ids = self._get_switch_name_id()
        for (category_line, data) in self._read_from_file(file_name):
            category = category_line.get(self._BACKUP_CATEGORY)
            statuses = {}
            if category and self._is_matched_url(category, Option.AdminSetting[1].get('switchGroup')) and type(data) \
                    == dict:
                self._create_switch_group_members(data, switch_name_ids)
        self._write_to_summary('Total count of restored Admin switch groups: backed-up: %s, '
                           'restored: (%s)\n' % (len(data), self._compose_status_summary(statuses)))

    def _restore_admin_port_group(self):
        self._write_to_summary()
        logger.info('Restoring Admin customized port groups ....')
        file_name = self._compose_file_name(Option.AdminSetting[2])
        prefix = '%s://%s/fm/fmrest/dbadmin' % (self._http_https, self._ip)
        names = []
        file_name = 'output_admin_settings.json'
        group_name_id = {}
        # retrieve the group name Id
        url = '%s/getAllAppGroups' % (prefix)
        (status, res) = self._send_request('GET', url)
        if res is not None:
            for entry in res:
                group_name_id.update({ entry.get('name'): entry.get('id') })

        switch_name_id = self._get_switch_name_id()
        switch_ports_info = {}
        for (category_line, data) in self._read_from_file(file_name):
            category = category_line.get(self._BACKUP_CATEGORY)
            statuses = {}
            if category:
                if self._is_matched_url(category, Option.AdminSetting[1].get('portGroup')):
                    port_group_name = category_line.get('portGroupName')
                    if not port_group_name:
                        # list of port groups
                        port_groups_url = '%s/AddAppGroup' % (prefix)
                        for port_group in data:
                            group_name = port_group.get('name')
                            payload = { 'newgroup': group_name}
                            # create port groups
                            (status, res) = self._send_request('POST', port_groups_url, payload,
                                                           content_type=self._PAYLOAD_CONTENT_TYPE.Form)
                            statuses.update({status: statuses.get(status, 0) + 1})
                            group_id = res.get('resultStatus') if res is not None else None
                            if group_id is not None and group_id != -1:
                                group_name_id.update({group_name: group_id})
                            logger.info('Restored Admin port group setting: %s ' % (group_name))
                        self._write_to_summary('Restored Admin port group settings: \n%s' % (group_name_id.keys()))
                        self._write_to_summary('Total count of restored port group setings: backed-up: %s, '
                                           'restored: (%s)\n' % (len(data), self._compose_status_summary(statuses)))
                    else:
                        port_group_url = '%s/addAppGroupMember' % (prefix)
                        port_group_db_id = group_name_id.get(port_group_name)
                        payload = { 'groupId': port_group_db_id }
                        members = []
                        for entry in data:
                            type = entry.get('type')
                            interface_name = entry.get('ifName')
                            switch_name = entry.get('swName')
                            switch_db_id = switch_name_id.get(switch_name)
                            port_db_id = None

                            # get the port/interface DB ID
                            switch_ports = switch_ports_info.get(switch_name) if switch_db_id is not None else None
                            if not switch_ports:
                                switch_ports = self._get_switch_ports(switch_db_id, type)
                                if switch_ports :
                                    switch_ports_info.update(switch_name, switch_ports)
                            if switch_ports:
                                for port in switch_ports:
                                    if interface_name == port.get('name'):
                                        port_db_id = port.get('dbId')
                                        members.append('%s:%s:%s' % (switch_db_id, port_db_id, type))
                        if members:
                            payload.update({'members[]': members})

                            # add members to port group
                            (status, res) = self._send_request('POST', port_group_url, payload,
                                                           content_type=self._PAYLOAD_CONTENT_TYPE.Form)
                            statuses.update({status: statuses.get(status, 0) + 1})
                            self._write_to_summary('Restored Admin port group member settings for group: \n%s' % (
                                port_group_name))
                        self._write_to_summary('Total count of restored port group member setings for group %s: '
                                               'backed-up: %s, '
                                           'restored: (%s)\n' % (port_group_name, len(data),
                                                                 self._compose_status_summary(
                            statuses)))

        logger.info('Restored Admin port group setting')

    def _create_switch_group_members(self, group_members, switch_name_ids, parent_group_id=-1):
        group_url = '%s://%s/fm/fmrest/dbadmin/addSwitchGroup' % (self._http_https, self._ip)
        move_member_url = '%s://%s/fm/fmrest/dbadmin/moveSwitchGroupMember' % (self._http_https, self._ip)
        if type(group_members) != dict:
            return
        for group_name, members in group_members.items():
            if group_name in ('Default_LAN', 'Default_SAN'):
                continue
            if group_name != 'leaf':
                payload = { 'groupName': group_name, 'parentGroupId': parent_group_id }
                self._send_request('POST', group_url, payload, content_type=self._PAYLOAD_CONTENT_TYPE.Form)
                group_id = self._get_switch_group_name_id().get(group_name)
                self._create_switch_group_members(members, switch_name_ids, group_id)
            else:
                 # move the members
                default_lan_group_id = self._get_switch_group_name_id().get('Default_LAN')
                for member in members:
                    switch_id = switch_name_ids.get(member)
                    if not switch_id:
                        continue

                    payload = { 'memType': 4, 'memDbId': switch_id, 'oldParentDbId': default_lan_group_id,
                                'newParentDbId': parent_group_id }
                    self._send_request('POST', move_member_url, payload,
                                               content_type=self._PAYLOAD_CONTENT_TYPE.Form)


    def _get_switch_group_name_id(self):
        switch_group_name_db_id = {}
        url = '%s://%s/fm/fmrest/dbadmin/getRbacGroupsInfo' % (self._http_https, self._ip)
        (status, res) = self._send_request('GET', url)
        if res is not None:
            for entry in res.get('items'):
                name = entry.get('name')
                id = entry.get('memDbId')
                switch_group_name_db_id.update({name: id})
                children = entry.get('children')
                if children:
                    for child in children:
                       switch_group_name_db_id.update({child.get('name'): child.get('memDbId')})
        return switch_group_name_db_id

    def _get_switch_name_id(self):
        switch_name_db_id = {}
        url = '%s://%s/fm/fmrest/inventory/switches' % (self._http_https, self._ip)
        (status, res) = self._send_request('GET', url)
        if res is not None:
            for entry in res:
                # if the switch is not discovered, the switch logical name is the same as IP
                ip = entry.get('ipAddress')
                name = entry.get('logicalName')
                if ip == name:
                    continue
                id = entry.get('switchDbID')
                if name is not None and id is not None:
                    switch_name_db_id.update({name: id})
        return switch_name_db_id

    def _get_switch_ports(self, switch_db_id, network_type=None):
        if not network_type:
            network_type = 'LAN'
        switch_ports = []
        if switch_db_id is not None:
            url = '%s://%s/fm/fmrest/inventory/getInterfacesBySwitch/?network=%s&switchDbID=%d' % (self._http_https,
                                                                                                   self._ip,
                                                                                                   network_type, switch_db_id)
            (status, res) = self._send_request('GET', url)
            if res is not None:
                for entry in res:
                    interface_name = entry.get('ifName')
                    interface_db_id = entry.get('interfaceDbId')
                    connect_to = entry.get('connectedTo')
                    if interface_name is not None and interface_db_id is not None:
                        switch_ports.append({'name': interface_name, 'dbId': interface_db_id, 'connectTo': connect_to})
        return switch_ports

    def _restore_auto_config_org_partition_network(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.OrgPartitionNetwork[0]):
            self._write_to_summary('Skip auto-config orgization/partition/network backup as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring auto-config organization/partition/network ....')
        file_name = self._compose_file_name(AutoConfigOption.OrgPartitionNetwork[2])
        names = []
        for (category_line, data) in self._read_from_file(file_name):
            category = category_line.get(self._BACKUP_CATEGORY)
            if category:
                names = []
                statuses = {}
                if self._is_matched_url(category, AutoConfigOption.OrgPartitionNetwork[1].get('org')):
                    fabric_name = category_line.get('fabricName')
                    if fabric_names and fabric_name not in fabric_names:
                        continue
                    # organizations
                    prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                    orgs_url = prefix + 'organizations'
                    for org in data:
                        (status, res) = self._send_request('POST', orgs_url, org)
                        org_name = org.get('organizationName')
                        names.append(org_name)
                        logger.info('Restored auto-config organization LAN fabric: %s, organization: %s.' % (
                            fabric_name, org_name))
                        statuses.update({status: statuses.get(status, 0) + 1})
                    self._write_to_summary('Restored auto-config organizations: \n%s' % (names))
                    self._write_to_summary('Total count of restored auto-config organizations: backed-up: %s, '
                                           'restored: (%s)\n' % (len(names), self._compose_status_summary(statuses)))
                elif self._is_matched_url(category, AutoConfigOption.OrgPartitionNetwork[1].get('partition')):
                    fabric_name = category_line.get('fabricName')
                    if fabric_names and fabric_name not in fabric_names:
                        continue
                    org_name = category_line.get('orgName')
                    # partitions
                    prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                    partitions_url = '%sorganizations/%s/partitions/' % (prefix, org_name)
                    for partition in data:
                        (status, res) = self._send_request('POST', partitions_url, partition)
                        partition_name = partition.get('partitionName')
                        names.append(partition_name)
                        logger.info('Restored auto-config organization LAN fabric: %s, organization: %s, '
                                    'partition: %s.' % (fabric_name, org_name, partition_name))
                        statuses.update({status: statuses.get(status, 0) + 1})
                    self._write_to_summary('Restored auto-config partitions for organization %s under LAN fabric %s: '
                                           '\n%s ' % (org_name, fabric_name, names))
                    self._write_to_summary('Total count of restored auto-config partitions for organization %s under '
                                           'LAN fabric %s: backed-up: %s, restored: (%s)\n' % (org_name, fabric_name,
                                            len(names), self._compose_status_summary(statuses)))
                elif self._is_matched_url(category, AutoConfigOption.OrgPartitionNetwork[1].get('network')):
                    fabric_name = category_line.get('fabricName')
                    if fabric_names and fabric_name not in fabric_names:
                        continue
                    org_name = category_line.get('orgName')
                    partition_name = category_line.get('partitionName')
                    # networks
                    prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                    networks_url = '%sorganizations/%s/partitions/%s/networks' % (prefix, org_name, partition_name)
                    for network in data:
                        (status, res) = self._send_request('POST', networks_url, network)
                        network_name = network.get('networkName')
                        names.append(network_name)
                        logger.info('Restored auto-config organization LAN fabric: %s, organization: %s, partition: '
                                    '%s, network: %s.' % (fabric_name, org_name, partition_name, network_name))
                        statuses.update({status: statuses.get(status, 0) + 1})
                    self._write_to_summary('Restored auto-config networks for partition %s of organization %s under '
                                           'LAN fabric %s: \n%s ' % (partition_name, org_name, fabric_name, names))
                    self._write_to_summary('Total count of restored auto-config networks for partition %s '
                                           'of organization %s under LAN fabric %s: backed-up: %s, restored: (%s)'
                                           '\n' % (partition_name, org_name, fabric_name, len(names), self._compose_status_summary(statuses)))
        self._write_to_summary('Restored auto-config organization/partition/network')

    def _restore_auto_config_profile(self):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.CustomizedProfile[0]):
            self._write_to_summary('Skip auto-config customized profile restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring auto-config customized profiles ....')
        file_name = self._compose_file_name(AutoConfigOption.CustomizedProfile[2])
        prefix = '%s://%s/rest/auto-config/' % (self._http_https, self._ip)
        profiles_url = prefix + 'profiles'
        # retrieve the existing profiles first
        (status, dest_profiles) = self._send_request('GET', profiles_url)

        profile_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, AutoConfigOption.CustomizedProfile[1]):
                # find out the customized profiles that are not the system packaged
                diff_profiles = data
                if dest_profiles:
                    # not include profileSubType as the newer version has the most up-to-date value
                    profiles_brief_data = [ {k: x[k] for k in ('profileName', 'profileType') } for x in data ]
                    profiles_brief_dest = [ {k: x[k] for k in ('profileName', 'profileType') } for x in dest_profiles ]
                    diff_profiles = [x for x in profiles_brief_data if x not in profiles_brief_dest]
                if diff_profiles:
                    for entry in data:
                        customized_profile = { k: entry[k] for k in ('profileName', 'profileType') }
                        if customized_profile in diff_profiles:
                            (status, res) = self._send_request('POST', profiles_url, entry)
                            profile_name = entry.get('profileName')
                            profile_names.append(profile_name)
                            logger.info('Restored auto-config customized profile: %s.' % (profile_name))
                            statuses.update({status: statuses.get(status, 0) + 1})
        self._write_to_summary('Restored customized auto-config profiles: \n%s' % (profile_names))
        self._write_to_summary('Total count of restored customized auto-config profiles: backed-up: %s, '
                                'restored: (%s)' % (len(profile_names), self._compose_status_summary(statuses)))

        self._write_to_summary('Restored auto-config customized profiles')

    def _restore_auto_config_end_host(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.EndHost[0]):
            self._write_to_summary('Skip auto-config end host restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring auto-config end hosts ....')
        file_name = self._compose_file_name(AutoConfigOption.EndHost[2])
        host_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            category = category_line.get(self._BACKUP_CATEGORY)
            if category:
                if self._is_matched_url(category, AutoConfigOption.EndHost[1]):
                    fabric_name = category_line.get('fabricName')
                    if fabric_names and fabric_name not in fabric_names:
                        continue
                    prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                    end_hosts_url = prefix + 'end-hosts'
                    for end_host in data:
                        (status, res) = self._send_request('POST', end_hosts_url, end_host)
                        host_name = end_host.get('name')
                        logger.info('Restored auto-config end host LAN fabric: %s, end host: %s.' % (
                            fabric_name, host_name))
                        logger.info('Restored %s auto-config end host' % (len(data)))
                        statuses.update({status: statuses.get(status, 0) + 1})
        self._write_to_summary('Restored end-hosts: \n%s' % (host_names))
        self._write_to_summary('Total count of restored end-hosts: backed-up: %s, '
                                'restored: (%s)' % (len(host_names), self._compose_status_summary(statuses)))

        self._write_to_summary('Restored auto-config end hosts')

    def _restore_auto_config_bl_pair(self, fabric_names):
         self._write_to_summary()
         if not self._is_option_set('autoconfigs', AutoConfigOption.BorderLeafPair[0]):
            self._write_to_summary('Skip auto-config border leaf pairing restore as its flag is not set in '
                        'configuration file.')
            return

         logger.info('Restoring auto-config border leaf pairing ....')
         file_name = self._compose_file_name(AutoConfigOption.BorderLeafPair[2])
         bl_er_names = []
         statuses = {}
         for (category_line, data) in self._read_from_file(file_name):
            if not data:
                 continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, AutoConfigOption.BorderLeafPair[1]):
                fabric_name = category_line.get('fabricName')
                if fabric_names and fabric_name not in fabric_names:
                    continue

                prefix = '%s://%s/rest/auto-config/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                bls_url = prefix + 'dci/paired-devices'
                for bl in data:
                    bl_fabric_name = bl.get('fabricName')
                    if not self._is_fabric_same(bl_fabric_name, fabric_name):
                        continue

                    # create BL/ER individually to avoid blocking by possible error on any request
                    logger.info('Duplicating Border Leaf pairing ER %s in LAN fabric %s ....' % (bl['name'], fabric_name))
                    dest_bl = { x : bl.get(x) for x in bl.keys() if x not in ['fabricName', 'partitionCount', 'dciPeer'] }
                    peers = bl['dciPeer']
                    if peers:
                        for peer in peers:
                            dest_peer = peer
                            dest_peer.pop('partitionCount', 0)
                            interface_name = peer.get('interfaceName')
                            if interface_name:
                                interfaces = interface_name.split('<->')
                                if len(interfaces):
                                    dest_bl.update({'interfaceName': interfaces[0].strip()})
                                    dest_peer.update({'interfaceName': interfaces[1].strip()})
                            dest_bl.update({'peer': dest_peer})
                            (status, res) = self._send_request("POST", bls_url, [dest_bl])
                            bl_er_names.append((bl['name'], peer['name']))
                            logger.info('Restored auto-config border leaf pairing LAN fabric: %s, border leaf: %s, '
                                'edge router: %s.' % (fabric_name, bl['name'], peer['name']))
                    else:
                        (status, res) = self._send_request("POST", bls_url, [dest_bl])
                        bl_er_names.append(bl['name'])
                        logger.info('Restored auto-config border leaf pairing LAN fabric: %s, '
                                'edge router/border PE: %s.' % (fabric_name, bl['name']))
                    statuses.update({status: statuses.get(status, 0) + 1})

                self._write_to_summary('Restored auto-config border leaf pairing: \n%s' % (bl_er_names))
                self._write_to_summary('Total count of restored auto-config border leaf pairing: backed-up: %s, '
                                       'restored: (%s)\n' % (len(data), self._compose_status_summary(statuses)))

         self._write_to_summary('Restored auto-config border leaf pairing')

    def _restore_auto_config_extended_partitions(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('autoconfigs', AutoConfigOption.ExtendedPartition[0]):
            self._write_to_summary('Skip auto-config extended partition restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring auto-config extended partitions ....')
        file_name = self._compose_file_name(AutoConfigOption.ExtendedPartition[2])
        # call ldapsearch to retrieve all the extended partitions
        cli = '/usr/bin/ldapadd -x -D "cn=admin,dc=cisco,dc=com" -h ' + self._ip + ' -w ' + self._pwd + ' -f ' + \
              file_name
        self._ssh_client.cmd(cli)
        self._write_to_summary('Restored auto-config extended partitions')

    def _restore_poap_dhcp_scope(self):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.DHCPScope[0]):
            self._write_to_summary('Skip POAP DHCP scopes restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring POAP DHCP scopes ....')
        file_name = self._compose_file_name(POAPOption.DHCPScope[2])

        # retrieve the existing DHCP scopes
        prefix = '%s://%s/rest/poap/' % (self._http_https, self._ip)
        scopes_url = prefix + 'dhcp/scopes'
        (status, existing_scopes) = self._send_request('GET', scopes_url)
        scope_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, POAPOption.DHCPScope[1]):
                for scope in data:
                    scope_name = scope.get('scopeName')
                    # DCNM does not allow enhanced_fabric_mgmt_scope update with different subnet

                    action = 'POST'
                    url = scopes_url
                    if any(d['scopeName'] == scope_name for d in existing_scopes):
                        action = 'PUT'
                        url = scopes_url + '/' + scope_name
                    (status, res) = self._send_request(action, url, scope)
                    scope_names.append(scope_name)
                    logger.info('Restored POAP DHCP scope: %s.' % (scope_name))
                    statuses.update({status: statuses.get(status, 0) + 1})
            self._write_to_summary('Restored POAP DHCP scopes: \n%s' % (scope_names))
            self._write_to_summary('Total count of restored POAP DHCP scopes: backed-up: %s, restored: (%s)\n' % (len(
                data), self._compose_status_summary(statuses)))
        self._write_to_summary('Restored POAP DHCP scopes')

    def _restore_poap_image_config_server(self):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.ImageConfigServer[0]):
            self._write_to_summary('Skip POAP image and config servers restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring POAP image and config servers ....')
        file_name = self._compose_file_name(POAPOption.ImageConfigServer[2])

        prefix = '%s://%s/rest/poap/' % (self._http_https, self._ip)
        servers_url = prefix + 'servers'
        (status, existing_servers) = self._send_request('GET', servers_url)

        server_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, POAPOption.ImageConfigServer[1]):
                for server in data:
                    server_name = server.get('serverName')
                    action = 'POST'
                    url = servers_url
                    if any(d['serverName'] == server_name for d in existing_servers):
                        action = 'PUT'
                        url = servers_url + '/' + server_name
                    (status, res) = self._send_request(action, url, server)
                    server_names.append(server_name)
                    logger.info('Restored POAP image and config server: %s.' % (server_name))
                    statuses.update({status: statuses.get(status, 0) + 1})
                self._write_to_summary('Restored POAP image and config servers: \n%s.' % (server_name))

        self._write_to_summary('Total count of restored POAP image and config servers: backed-up: %s, restored: ('
                               '%s)\n' % (len(server_names), self._compose_status_summary(statuses)))
        self._write_to_summary('Restored POAP image and config servers')

    def _restore_poap_setting_file(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.SettingFile[0]):
            self._write_to_summary('Skip POAP setting file restore as its flag is not set in '
                        'configuration file.')
            return

        logger.info('Restoring POAP setting files ....')
        file_name = self._compose_file_name(POAPOption.SettingFile[2])

        setting_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, POAPOption.SettingFile[1]):
                fabric_name = category_line.get('fabricName')
                if fabric_names and fabric_name not in fabric_names:
                    continue

                prefix = '%s://%s/rest/poap/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                settings_url = prefix + 'settings'

                for setting in data:
                    (status, res) = self._send_request('POST', settings_url, setting)
                    logger.info('Restored POAP setting: %s.' % (setting.get('templateSettingsName')))
                    setting_names.append(setting.get('templateSettingsName'))
                    statuses.update({status: statuses.get(status, 0) + 1})

                self._write_to_summary('Restored POAP setting files: \n%s' % (setting_names))
                self._write_to_summary('Total count of restored POAP setting files: backed-up: %s, restored: (%s)\n' % (
                    len(data), self._compose_status_summary(statuses)))
        self._write_to_summary('Restored POAP setting files')

    def _restore_poap_definition(self, fabric_names):
        self._write_to_summary()
        if not self._is_option_set('poaps', POAPOption.Definition[0]):
            self._write_to_summary('Skip POAP definition restore as its flag is not set in configuration file.')
            return

        logger.info('Restoring POAP definition data ....')
        file_name = self._compose_file_name(POAPOption.Definition[2])

        # refresh the nav group list
        self._compose_restored_nav_groups()

        switch_names = []
        xmpp_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, POAPOption.Definition[1]):
                fabric_name = category_line.get('fabricName')
                if fabric_names and fabric_name not in fabric_names:
                    continue

                serial_number = category_line.get('serialNumber')
                prefix = '%s://%s/rest/poap/%s' % (self._http_https, self._ip, self._compose_fabric_prefix(fabric_name))
                defs_url = prefix + 'definitions/'

                switch_detail = self._compose_switch_payload(data, fabric_name)
                template_detail = self._compose_template(data)

                switch_name = switch_detail.get('switchName')
                if template_detail:
                    poap_def = { 'switchDetails': [ switch_detail ],
                             'templateDetails': [ template_detail ]
                            }
                    (status, res) = self._send_request('POST', defs_url, poap_def)
                else:
                    # /definitions URI does not handle config upload correctly. use workaround instead
                    switch_detail.update({'methodType': 'POST'})
                    poap_def = { 'poapSwitchCol': [ switch_detail ]}
                    defs_url = prefix + 'switch-definitions/upload'
                    print '\n !==== poap_def: \n', poap_def
                    (status, res) = self._send_request('POST', defs_url, poap_def)

                switch_names.append(switch_name)
                logger.info('Restored POAP definition for switch %s (serial %s).' % (switch_name, serial_number))
                statuses.update({status: statuses.get(status, 0) + 1})

                # create XMPP user for the switch
                template_params = template_detail.get('templateParams') if template_detail else None
                xmpp_pwd = template_params.get('XMPP_PASSWORD') if template_params else None
                if xmpp_pwd:
                    cli = 'appmgr add_user xmpp -u %s -p %s' % (switch_name, xmpp_pwd)
                    self._ssh_client.cmd(cli)
                    logger.info('Created XMPP user for switch %s.' % (switch_name))
                    xmpp_names.append(switch_name)

        self._write_to_summary('Restored POAP definitions for switches: \n%s' % (switch_names))
        self._write_to_summary('Restored XMPP users for switches: \n%s' % (xmpp_names))
        self._write_to_summary('Total count of restored POAP definitions: backed-up: %s, restored: (%s), created XMPP '
                               'users: %s\n' % (len(switch_names), self._compose_status_summary(statuses),
                                                len(xmpp_names)))
        self._write_to_summary('Restored POAP definitions')

    def _restore_poap_template(self):
        self._write_to_summary()
        is_all_templates = self._is_option_set('poaps', POAPOption.AllTemplate[0])
        is_poap_definitions = self._is_option_set('poaps', POAPOption.Definition[0])
        if not is_all_templates and not is_poap_definitions:
            self._write_to_summary('Skip POAP template restore as its flag is not set in configuration file.')
            return

        logger.info('Restoring POAP template data ....')
        # only need to restore from one template backup file
        file_name = self._compose_file_name(POAPOption.AllTemplate[2])

        template_names = []
        statuses = {}
        for (category_line, data) in self._read_from_file(file_name):
            if not data:
                continue
            category = category_line.get(self._BACKUP_CATEGORY)
            if self._is_matched_url(category, POAPOption.AllTemplate[1]):
                template_name = category_line.get('templateName')

                templates_url = '%s://%s/fm/fmrest/config/templates' % (self._http_https, self._ip)
                template_content = data.get('content')
                if not template_content:
                    continue

                # Non_Fabric_Switch_v3 template
                if template_name == 'Non_Fabric_Switch_v3':
                    # insert NTP_SERVER and DNS_SERVER definition
                    key = 'All rights reserved.'
                    index = template_content.find(key)
                    if index > 0:
                        index += len(key)
                        original_content = template_content
                        param_def = '''\n\n@(IsMandatory=true, DisplayName="NTP Server", Section="General")
string NTP_SERVER; \n\n@(IsMandatory=true, DisplayName="DNS Server", Section="General")
string DNS_SERVER; '''
                        template_content = original_content[:index] + param_def + original_content[index:]

                (status, res) = self._send_request('POST', templates_url, template_content,
                                                   content_type=self._PAYLOAD_CONTENT_TYPE.Plain)
                template_names.append(template_name)
                logger.info('Restored POAP template %s.' % (template_name))
                statuses.update({status: statuses.get(status, 0) + 1})

        self._write_to_summary('Restored POAP templates: \n%s' % (template_names))
        self._write_to_summary('Total count of restored POAP template: backed-up: %s, restored: (%s)\n' % (len(
            template_names), self._compose_status_summary(statuses)))
        self._write_to_summary('Restored POAP templates')

    def _compose_switch_payload(self, switch_def, fabric_name):
        keys = ['serialNumber', 'deviceType', 'configServerId', 'systemImageName', 'kickstartImageName',
                'imageServerId', 'lanGroup', 'switchName', 'mgmtIp', 'username','password' ]
        switch_payload = {x : switch_def.get(x) for x in keys}
        # update the nav group Id based on the destination DCNM's LAN fabric group Id
        nav_group_id = self._get_group_id(fabric_name)
        if nav_group_id:
            switch_payload.update({'lanGroup': nav_group_id})

        # overrides
        if self._override_options and self._override_options.get('poapImageOverride'):
            image_overrides = self._override_options.get('poapImageOverride')
            for (current_image, new_image) in image_overrides.items():
                system_image = switch_def.get('systemImageName')
                kickstart_image = switch_def.get('kickstartImageName')
                print '\n\n !!!! sys_image: ', system_image
                print '\n\n !!!! kickstart_image: ', kickstart_image

                if system_image and system_image == current_image:
                    switch_payload.update({'systemImageName': new_image})
                if kickstart_image and kickstart_image == current_image:
                    switch_payload.update({'kickstartImageName': new_image})
        switch_payload.update({'publish': 'true'})
        if switch_def.get('uploadFileContent') and switch_def.get('uploadFileName'):
            switch_payload.update({'uploadFileName': switch_def.get('uploadFileName')})
            switch_payload.update({'uploadFileContent': switch_def.get('uploadFileContent')})

        return switch_payload

    def _compose_template(self, switch_def):
        #setting_override_file = poap_backup_option_param.get('settingoverridefile') if poap_backup_option_param else
        #  None
        #overriden_settings = self. _compose_template_variable_from_setting_file(setting_override_file)

        setting_overrides = self._override_options.get('poapSettingOverride')
        template_name = switch_def.get('templateName')
        nv_pairs = None

        template_payload = {}
        if switch_def.get('templateNVPairs'):
            input_nv_pairs = json.loads(switch_def.get('templateNVPairs'))
            nv_pairs = self._parse_poap_nvpair(input_nv_pairs)

            for key in nv_pairs.keys():
                if key[0].islower():
                    nv_pairs.pop(key, 0)
                    continue

                '''if '_ARRAY' in key or key in ['PORT_CHANNEL_HOSTS', 'HOST_INTERFACE_STRUCTURE']:
                    value0 = nv_pairs.get(key)
                    if key == 'FEX_ARRAY':
                        print '\n value0: ', value0
                    value = '{"' + key + '":' + value0 + '}'
                    nv_pairs.update({key: value})'''

                if setting_overrides and (key.lower() in setting_overrides):
                    value = setting_overrides.get(key.lower())
                    nv_pairs.update({key: value})

            # template name
            template_overrides = self._override_options.get('poapTemplateOverride')

            if template_overrides:
                for (old_template, new_template) in template_overrides.items():
                    print '\n !! template name: ', template_name
                    if re.match (old_template.strip(), template_name):
                        template_name = new_template
                        print '\n !! Now again template name: ', template_name

            template_payload = {
                'templateName': template_name,
                'templateParams': nv_pairs
            }
        return template_payload

    def _parse_poap_nvpair(self, nv_pairs):
        # check whether DCNM 7 NV pair format (contains annotation) or DCNM 10 version of NVPair
        if not nv_pairs:
            return
        if type(nv_pairs[nv_pairs.keys()[0]]) != dict:
            # DCNM 10 version of NVPair
            return nv_pairs

        # DCNM 7.2 version of NVPair
        normalized_nv_pairs = {}
        for (index, info) in nv_pairs.items():
            name = info.get('name')
            value = info.get('text')
            normalized_nv_pairs.update({name:value.strip() if value else ''})
        return normalized_nv_pairs

    def _extract_access_info(self, dcnm_param):
        ip = dcnm_param.get('ip')
        user = dcnm_param.get('user')
        passwd = dcnm_param.get('password')
        https = self._extract_http_https(dcnm_param.get('https'))
        return (ip, user, passwd, https)

    def _extract_http_https(self, https):
        return 'https' if (not https or https == '1') else 'http'

    def _compose_fabric_prefix(self, fabric_name):
        #return ('fabrics/' + fabric_name + '/') if (fabric_name and fabric_name.lower() != 'default_lan') else ''
        # POAP definition returns the complete list if fabric is not specified
        return ('fabrics/' + fabric_name + '/') if (fabric_name) else ''

    def _compose_file_name(self, category):
        file_prefix = self._option_params.get('backupoutputfileprefix')
        if not file_prefix:
            file_prefix = 'output'
        file_name = file_prefix.rsplit('.', 1)[0]
        if category:
            file_name += '_' + category
        file_name += '.json'
        return self._dest_folder + '/' + file_name

    def _compose_separator(self, url, **kwargs):
        sep = { self._BACKUP_CATEGORY: url } if url else {}
        if kwargs:
            for key in kwargs.keys():
                sep.update({key: kwargs.get(key)})
        return sep

    def _write_to_file(self, file_name, data, separator=None, is_overwrite=False):
        mode = 'w' if is_overwrite else 'a'
        if data:
            with open(file_name, mode) as f:
                if separator:
                    f.write('\n\n')
                    json.dump(separator, f)
                    f.write('\n')
                json.dump(data, f)
            logger.info('Saved data to file %s.' % (file_name))
        else:
            logger.info('Empty data to be saved to file.')

    def _write_init(self, file_name):
        with open(file_name, 'w'):
            pass

    def _read_from_file(self, file_name):
        if not os.path.isfile(file_name):
            return

        category_line = None
        # create an empty file if does not exist
        with open(file_name, 'r') as f:
            for line in f:
                if line.strip('\n'):
                    line = json.loads(line)
                    if type(line) == dict and (self._BACKUP_CATEGORY in line):
                        category_line = line
                    elif category_line:
                        yield (category_line, line)

    def _compose_summary_file(self):
        now = datetime.datetime.now()
        timestamp = now.strftime('%Y%m%d_%H%M%S')
        return 'dcnm_backup_restore_summary_' + timestamp + '.log'

    def _compose_status_summary(self, statuses):
        if not statuses or type(statuses) != dict:
            return
        summary = ''
        for (status_code, count) in statuses.items():
            summary += '%s: %s' % (status_code, count)
        return summary

    def _write_to_summary(self, msg=None):
        file_name = self._summary_file
        with open(file_name, 'a') as f:
            if not msg:
                f.write('---------------------------\n')
            else:
                f.write(msg + '\n')
                logger.info('[Summary] ' + msg)

    def _merge_fabric_names(self, retrieved_fabric_names, input_params = None):
        fabric_names = retrieved_fabric_names
        if self._fabric_names:
            input_fabric_names = self._fabric_names
            if type(self._fabric_names) == str:
                input_fabric_names = [ self._fabric_names ]
            fabric_names = set(retrieved_fabric_names).intersection(input_fabric_names)
        return fabric_names

    def _get_group_id(self, fabric_name):
        if not self._restored_nav_groups:
            self._compose_restored_nav_groups()

        if not self._restored_nav_groups:
            return
        for (key, value) in self._restored_nav_groups.items():
            if value.strip().lower() == fabric_name.strip().lower():
                return key

    def _compose_restored_nav_groups(self):
        url = '%s://%s/fm/fmrest/device-group/groups' % (self._http_https, self._ip)
        (status, res) = self._send_request('GET', url, None, 'Get Device Group ID')
        if not res:
            return
        groups = res.get('row') if res.get('row') else res.get('rows')
        if not groups:
            return
        for group in groups:
            entry = group.get('entry')
            if entry:
                self._restored_nav_groups.update({entry[0]: entry[1]})

    def _is_fabric_same(self, fabric1, fabric2):
        if not fabric1:
            if not fabric2 or fabric2.lower() == 'default_lan':
                return True
        else:
            if fabric2 and fabric2.lower() == fabric1.lower():
                return True
        return False

    def _is_option_set(self, key, value = '1', options = None):
        if not options:
            options = self._option_params
        if not key or not options.get(key):
            return False
        entry = options.get(key)
        types = type(entry)
        if types == str:
            return entry.lower() == value.lower()
        elif types == list:
            return value.lower() in (map(str.lower, entry))

        return False

    def _is_existing_key_value(self, dicts, key, value):
        found = False
        try:
            if dicts:
                for tmp in dicts:
                    if tmp.get(key) == value:
                        found = True
                        break
        except Exception as e:
            logger.error('[_is_contain] Input parameter is not of array of dictionary')
        return found

    def _is_dcnm_10(self):
        return self._http_https == 'https'

    def _is_matched_url(self, src, other):
        if not src or not other:
            return False
        elif src == other or src +'/' == other or src == other + '/':
            return True
        else:
            return False

    def _compose_template_variable_from_setting_file(self, setting_override_file):
        if not setting_override_file:
            logger.info('Skip setting overriding as no setting override file is specified in configuration file.')
            return
        json_params = {}
        try:
            with open(setting_override_file) as f:
                for line in f:
                    try:
                        if line.strip():
                            key, value = line.split(':', 1)
                    except Exception as e:
                        logger.error('Invalid line: ' + line + ' Error: ' + str(e))
                        continue
                    key = key.strip()
                    value = value.strip()
                    if '_ARRAY' in key or key in ['PORT_CHANNEL_HOSTS', 'HOST_INTERFACE_STRUCTURE']:
                            value = '{"' + key + '":' + value + '}'
                    json_params.update({key: value.strip()})
        except Exception as e:
            logger.exception(str(e))
        return json_params

    def _get_fabric_names(self):
        # loop thru LAN fabrics
        fabric_names = ['Default_LAN']
        if self._is_dcnm_10():
            fabrics_url = '%s://%s/rest/fabrics?detail=True' % (self._http_https, self._ip)
            (status, fabrics) = self._send_request('GET', fabrics_url)
            if fabrics:
                for fabric in fabrics:
                    fabric_names.append(fabric['name'])
        # merge with input fabric names
        merged_fabric_names = self._merge_fabric_names(fabric_names)
        # return list of source LAN fabrics
        return merged_fabric_names

    def _extract_soap_content(self, url, request_body):
        # return list
        if not url or not request_body:
            return
        list_data = []
        res_soap = self._send_request_soap(url, request_body)
        if res_soap:
            doc = et.fromstring(res_soap)
            columns = doc.findall('.//column')
            rows = doc.findall('.//row')
            for row in rows:
                values = row.findall('entry')
                row_detail = {}
                for column, value in zip(columns, values):
                   row_detail.update({column.text: value.text})
                list_data.append(row_detail)
        return list_data

    def _send_request(self, operation, url, payload=None, desc='', content_type='JSON'):
        """Generalize the HTTP(S) request, which includes POST, PUT, DELETE.

        :param str operaion: The HTTP verb with value of POST, PUT or DELETE.
        :param str url: The URI that the request is sent to.
        :param dict payload: The data to be put in the request body. It will
                                be converted into JSON format before being sent out.
        :param str desc: The description to be recorded in log message.
        :returns: status - integer to indicate whether the status is successful (0), failure (1)
        requests.models.Response -- The response object from HTTP(S) request.

        :notes: It logs into DCNM, sends HTTP(S) request, and log out from DCNM.

        """

        res = None
        res_json = None
        status_code = self._STATUS_CODES.InvalidRequest
        try:
            payload_json = payload
            if payload and content_type == self._PAYLOAD_CONTENT_TYPE.JSON:
                payload_json = json.dumps(payload)
                logger.debug('[send_request] Request payload: %s' % (payload_json))

            if payload and type(payload) == dict and payload.get('templateDetails') and payload.get('templateDetails')[\
                    0].get(
                    'uploadFileContent'):
                print '\n === inside send_req: \n', payload_json
                print '!!!\n'

            self._login()
            if content_type == self._PAYLOAD_CONTENT_TYPE.Plain:
                # set text/plain type
                self._req_headers.update({'Content-Type': 'text/plain'})
            elif content_type == self._PAYLOAD_CONTENT_TYPE.Form:
                self._req_headers.update({'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'})

            if operation == 'POST':
                res = requests.post(url, data=payload_json, headers=self._req_headers, timeout=self._TIMEOUT_RESPONSE,
                                    verify=False)
                desc += ' creation'
            elif operation == 'PUT':
                res = requests.put(url, data=payload_json, headers=self._req_headers, timeout=self._TIMEOUT_RESPONSE,
                                   verify=False)
                desc += ' update'
            elif operation == 'DELETE':
                res = requests.delete(url, data=payload_json, headers=self._req_headers, timeout=self._TIMEOUT_RESPONSE,
                                      verify=False)
                desc += ' deletion'
            elif operation == 'GET':
                res = requests.get(url, data=payload_json, headers=self._req_headers, timeout=self._TIMEOUT_RESPONSE,
                                   verify=False)
                desc += ' retrieval'

            if res and res.status_code >= 200:
                logger.info('[DCNMClient] Sent %s to %s successfully.' % (desc, url))
                res_json = res.json()
                status_code = self._STATUS_CODES.Success
            else:
                logger.error('[DCNMClient] Sent %s to %s unsuccessfully %d.\n Response: %s' % (
                desc, url, res.status_code, res.text))
                if res.text and 'already exists' in res.text.lower():
                    status_code = self._STATUS_CODES.AlreadyExists
                else:
                    status_code = self._STATUS_CODES.Failure
            self._logout()
        except requests.ConnectionError as e:
            # add url to the exception for caller to display
            print 'Error connecting to ', url
            logger.exception(str(e))
            status_code = self._STATUS_CODES.ConnectionError
            raise
        except requests.HTTPError as e:
            print 'HTTP error'
            logger.exception(str(e))
            status_code = self._STATUS_CODES.HttpError
        except requests.Timeout as e:
            print 'Timeout error'
            logger.exception(str(e))
            status_code = self._STATUS_CODES.TimeoutError
        except ValueError:
            res_json = None
            status_code = self._STATUS_CODES.ValueError
        finally:
            if content_type != self._PAYLOAD_CONTENT_TYPE.JSON:
                self._req_headers.update({'Content-Type': 'application/json; charset=UTF-8'})

        logger.debug('[send_request] Response payload: %s' % (res_json))
        return (status_code, res_json)

    def _send_request_soap(self, url, body):
        res_soap = None
        self._login()
        http_header = { 'content-type': 'text/xml' }
        soap_env = """<?xml version="1.0" encoding="UTF-8"?>
                    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
                    xmlns:ns0="http://dcnm/headers" xmlns:tns="http://ep.san.jaxws.dcbu.cisco.com/">"""
        soap_header = '<SOAP-ENV:Header><ns0:token>%s</ns0:token></SOAP-ENV:Header>' % (self._req_headers.get('Dcnm-Token').strip())
        soap_body = '<SOAP-ENV:Body>%s</SOAP-ENV:Body></SOAP-ENV:Envelope>' % (body)
        res = requests.post(url, data=soap_env + soap_header + soap_body, headers=http_header)
        if res and res.status_code >= 200:
            res_soap = res.content
        self._logout()
        return res_soap

    def _login(self):
        """Log into DCNM by calling POST request to DCNM logon resource.

        DCNM returns DCNM token in login response after successful login, and that token
        will be added to the class request header field to be used for subsequent
        request composition.

        """

        url_login = '%s://%s/rest/logon' % (self._http_https, self._ip)

        expiration_time = 100000

        payload = {'expirationTime': expiration_time}
        self._req_headers = {'Accept': 'application/json', 'Content-Type': 'application/json; charset=UTF-8'}
        res = requests.post(url_login, data=json.dumps(payload), headers=self._req_headers, auth=(self._user, self._pwd),
                            timeout=self._TIMEOUT_RESPONSE, verify=False)
        logger.debug('[DCNMClient] Login response: %s' % (res.content))
        session_id = ''
        if res and (res.status_code >= 200) and res.json():
            session_id = res.json().get('Dcnm-Token')
        # update global request header
        self._req_headers.update({'Dcnm-Token': session_id})

    def _logout(self):
        """Log out from DCNM by calling POST request to DCNM logout resource
        """

        url_logout = '%s://%s/rest/logout' % (self._http_https, self._ip)
        requests.post(url_logout, headers=self._req_headers, timeout=self._TIMEOUT_RESPONSE, verify=False)

class SSHClient:
        """ This SSH client runs CLI on remote host via SSH protocol.
        """

        def __init__(self, ip, user, passwd, port=22):
                """Create a new instance of SSH client.

                :param str ip: The remote host IP address.
                :param str user: The user name.
                :param str passwd: The user's password.
                :param int port: The port to be connected to.

                """

                self.ip = ip
                self.user = user
                self.passwd = passwd
                self.port = port


        def run_cmd(self, c):
                """Run CLI command

                :param str c: The command to be running on remote host.
                :returns: tuple (pid, fd) -- (child's process Id, file descriptor to
                                        control child's terminal)

                """

                (pid, f) = pty.fork()
                if pid == 0:
                        os.execlp('ssh', 'ssh', '-p %d' % self.port, self.user + '@' + self.ip, c)
                else:
                        return (pid, f)

        def pop_dir(self, src, dst, is_dir = True):
                """Copy surce file on remote host to destination .

                :param str src: Source file name
                :param str dst: Destination file
                :returns:  tuple (pid, fd) -- (child's process Id, file descriptor
                                        connected to child's controlling terminal)

                """

                (pid, f) = pty.fork()
                if pid == 0:
                    if is_dir:
                        os.execlp("scp", "scp", '-rp', '-P %d' % self.port, self.user + '@' + self.ip + ':' + src, dst)
                    else:
                        os.execlp("scp", "scp", '-P %d' % self.port, self.user + '@' + self.ip + ':' + src, dst)
                else:
                        return (pid, f)


        def push_dir(self, src, dst, is_dir = True):
                """Copy surce file to destination on remote host.

                :param str src: Source file name
                :param str dst: Destination file
                :returns:  tuple (pid, fd) -- (child's process Id, file descriptor
                                        connected to child's controlling terminal)

                """

                (pid, f) = pty.fork()
                if pid == 0:
                    if is_dir:
                        os.execlp("scp", "scp", '-rp', '-P %d' % self.port, src, self.user + '@' + self.ip + ':' + dst)
                    else:
                        os.execlp("scp", "scp", '-P %d' % self.port, src, self.user + '@' + self.ip + ':' + dst)
                else:
                        return (pid, f)

        def _read(self, f):
                """Read content from file descriptor.

                :param fd f: File descriptor to read from.
                :returns: str -- At most 1K bytes string read from the file descriptor.

                """

                x = ''
                try:
                        x = os.read(f, 1024)
                except Exception, e:
                        # this always fails with io error
                        pass
                return x


        def ssh_results(self, pid, f):
                """Retrive SSH command execution result.

                :param pid pid: Process Id.
                :param fd f: File descriptor.
                :retuns: str -- The SSH command execution result string.

                """

                output = ""
                got = self._read(f)
                # check for authenticity of host request
                m = re.search("authenticity of host", got)
                if m:
                        os.write(f, 'yes\n')
                        # Read until we get ack
                        while True:
                                got = self._read(f)
                                m = re.search("Permanently added", got)
                                if m:
                                        break

                        got = self._read(f)
                # check for passwd request
                m = re.search("assword:", got)
                if m:
                        # send passwd
                        os.write(f, self.passwd + '\n')
                        # read two lines
                        tmp = self._read(f)
                        tmp += self._read(f)
                        m = re.search("Permission denied", tmp)
                        if m:
                                raise ValueError, '[SSHClient] Invalid passwd'
                        # passwd was accepted
                        got = tmp
                while got and len(got) > 0:
                        output += got
                        got = self._read(f)
                os.waitpid(pid, 0)
                os.close(f)
                return output


        def cmd(self, c):
                """Run SSH command.

                :param str c: command to be run on remote host
                :returns: str -- The SSH command execution result string.

                """

                (pid, f) = self.run_cmd(c)
                return self.ssh_results(pid, f)


        def pop(self, src, dst):
                """Copy directory or file to remote host.

                :param str src: Source directory or file.
                :param str dst: Destination directory or file.

                :returns: str -- The SSH command execution result string.

                """

                s = os.stat(dst)
                if stat.S_ISDIR(s[stat.ST_MODE]):
                        (pid, f) = self.pop_dir(src, dst, True)
                else:
                        (pid, f) = self.pop_dir(src, dst, False)
                return self.ssh_results(pid, f)


        def push(self, src, dst):
                """Copy directory or file to remote host.

                :param str src: Source directory or file.
                :param str dst: Destination directory or file.

                :returns: str -- The SSH command execution result string.

                """

                s = os.stat(src)
                if stat.S_ISDIR(s[stat.ST_MODE]):
                        (pid, f) = self.push_dir(src, dst, True)
                else:
                        (pid, f) = self.push_dir(src, dst, False)
                return self.ssh_results(pid, f)


def read_config_file(config_file):
    """Read initial configuration file.

    :param config_file: Configuration file name.

    """

    config_params = {}

    parser = ConfigParser.ConfigParser()
    parser.readfp(open(config_file))

    for section in parser.sections():
        section_params = {}
        for option in parser.options(section):
            values = parser.get(section, option)
            if ';' in values:
                values = map(str.strip, values.split(';'))
            section_params.update({option: values})
        config_params.update({section: section_params})

    return config_params


def set_logger():
    """Set logger with log file name and log message format.

    The log messages are written to 'vcdclient.log' file.
    """

    default_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler_console = StreamHandler()
    handler_console.setFormatter(default_formatter)
    handler_console.setLevel(logging.DEBUG)

    handler_file = FileHandler('dcnm_backup_restore.log', 'a')
    handler_file.setFormatter(default_formatter)

    logger.addHandler(handler_console)
    logger.addHandler(handler_file)

def compose_backup_folder():
    now = datetime.datetime.now()
    timestamp = now.strftime('%Y%m%d_%H%M%S')
    return 'dcnm_backup_' + timestamp

def create_archive_file(zip_file, folder):
    shutil.make_archive(zip_file, 'gztar', root_dir='.', base_dir=folder, logger=logger)
    shutil.rmtree(folder)

def extract_archive_file(zip_file):
    if (zip_file.endswith("tar.gz")):
        tar = tarfile.open(zip_file, "r:gz")
        tar.extractall()
        tar.close()
    elif (zip_file.endswith("tar")):
        tar = tarfile.open(zip_file, "r:")
        tar.extractall()
        tar.close()

if __name__ == '__main__':
    """Main function to backup POAP data. """

    set_logger()

    param_size = len(sys.argv)
    if (param_size == 1):
        print 'Usage1: python backupRestoreDCNM.py configFileName \n Usage2: python backupRestoreDCNM.py ' \
              'configFileName archiveFile'
        exit(1)
    config_file = sys.argv[1]
    #config_file = 'backup_restore_ini_xto.conf'
    #config_file = 'restore_ini_xto.conf'

    try:
        config_params = read_config_file(config_file)

        # set logger level
        log_levels = {
                'DEBUG': logging.DEBUG,
                'INFO': logging.INFO,
                'WARNING': logging.WARNING,
                'ERROR': logging.ERROR,
                'CRITICAL': logging.CRITICAL
        }
        params_log = config_params.get('Log')
        log_level = 'INFO'
        if params_log:
            log_level = params_log.get('level', 'INFO')
        logger.setLevel(log_levels.get(log_level, logging.INFO))

        # get config params
        dcnm_params = config_params.get('DCNM')
        option_params = config_params.get('Option')
        fabric_override_params = config_params.get('FabricOverride')
        poap_image_override_params = config_params.get('POAPOverride:Image')
        poap_template_override_params = config_params.get('POAPOverride:Template')
        poap_setting_override_params = config_params.get('POAPOverride:Setting')

        override_params = {'fabricOverride': fabric_override_params,
                           'poapImageOverride': poap_image_override_params,
                           'poapTemplateOverride': poap_template_override_params,
                           'poapSettingOverride': poap_setting_override_params }
        action = option_params.get('action', 'backup')
        is_backup = True if action.lower() == 'backup' else False
        if not is_backup and param_size == 2:
            print 'Usage2: python backupRestoreDCNM.py configFileName archiveFile'
            exit(1)

        archive_file_base = ''
        folder = ''

        if is_backup:
            folder = compose_backup_folder()
            os.makedirs(folder)
            archive_file_base = folder
        else:
            archive_file = sys.argv[2]
            archive_file_base = os.path.basename(archive_file).split('.')[0]
            folder = archive_file_base
            extract_archive_file(archive_file)

        dcnm_client = DCNMClient(dcnm_params, folder, option_params, override_params)
        if is_backup:
            dcnm_client.backup_admin_setting()
            # always backup nav groups for association with fabric name
            dcnm_client.backup_folder()
            dcnm_client.backup_nav_groups()
            dcnm_client.backup_general_setting()
            dcnm_client.backup_lan_fabrics()

            dcnm_client.backup_auto_config_data()
            dcnm_client.backup_poap_data()
            # zip the files
            create_archive_file(archive_file_base, folder)
        else:
            #dcnm_client.restore_folder()
            dcnm_client.restore_general_setting()
            dcnm_client.restore_lan_fabrics()

            dcnm_client.restore_auto_config_data()
            dcnm_client.restore_poap_data()
            dcnm_client.restore_admin_setting()

        logger.info('Exit the program.\n')

    except Exception as e:
        logger.exception(str(e))
        logger.info('Exit the program.\n')
        exit(1)
