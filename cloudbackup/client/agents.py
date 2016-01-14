"""
Rackspace Cloud Backup Agent API
"""
from __future__ import print_function

import datetime
import json
import logging
import requests
import time
import threading

from cloudbackup.common.command import Command

requests.packages.urllib3.disable_warnings()


class ParameterError(Exception):
    """
    Parameter Error Exception
    """
    pass


# function for Agents class to use to keep a given agent awake
def _keep_agent_wake_thread_fn(my_notifier=None, user=None, apikey=None,
                               rse_app=None, rse_version=None,
                               rse_agentkey=None, rse_log=None,
                               rse_apihost=None, rse_period=None, apihost=None,
                               agent_id=None, api_version=None,
                               project_id=None):
    """
    (Internal) Thread function that will periodically post the wake agent message and look for the specified agent
    Aside from my_notifier, the function maintains its own objects internally in thread local data storage for thread-safety purposes

    Require parameters:
        my_notifier - threading.Event object instance that signals thread termination
        user - username for Keystone/Identity authentication
        apikey - apikey for Keystone/Identity authentication
        rse_app - RSE Application Name
        rse_version - RSE Application Version
        rse_agentkey - RSE Channel to listen to
        rse_period - period between wake agent calls
        apihost - Rackspace Cloud Backup API URL
        agent_id -  machine agent identifier for the agent to monitor for

    Option parameters:
        rse_log - Base log file name, the thread will append data to create a unique RSE log file name for the thread's RSE queries. If not desired, specify None
        rse_apihost - RSE API URL See cloudbackup.clients.rse.Rse for details
    """
    if None in (my_notifier, user, apikey, rse_app, rse_version, rse_agentkey, rse_period, apihost, agent_id):
        raise RuntimeError('Invalid parameters. Some optional parameters were not properly specified')

    log = logging.getLogger(__name__)

    # For threading simplicity we are going to create thread local version of each of the required objects
    import cloudbackup.client.auth
    import cloudbackup.client.rse
    data = threading.local()
    data.thread_id = threading.current_thread().ident
    data.log_prefix = 'RSE Wakeup Thread[{0:}] Log'.format(data.thread_id)
    data.auth_engine = cloudbackup.client.auth.Authentication(user, apikey)
    data.agent_engine = cloudbackup.client.agents.Agents(True, data.auth_engine,
                                                         apihost, api_version,
                                                         project_id)
    data.logfile = None
    if rse_log is not None:
        data.logfile = '{0:}.thread_{1:}'.format(rse_log, data.thread_id)

    log.debug('{0:}: {1:}'.format(data.log_prefix, data.logfile))
    log.debug('{0:}: Agent Id - {1:}'.format(data.log_prefix, agent_id))
    log.debug('{0:}: RSE Period - {1:}'.format(data.log_prefix, rse_period))

    data.rse_engine = cloudbackup.client.rse.Rse(rse_app, rse_version,
                                                 data.auth_engine, data.agent_engine,
                                                 rse_agentkey, logfile=data.logfile,
                                                 apihost=rse_apihost,
                                                 api_version=api_version,
                                                 project_id=project_id)

    def __check_notifier(notifier):
        """
        Simple wrapper to check the notifier and return whether or not the loop should exit

        Parameters:
            notifier - threading.Event object instance

        Returns:
            True if the loop should continue (event is not set)
            False if the loop should terminate (event is set)
        """
        if notifier.is_set():
            notifier.clear()
            log.debug('{0:}: Detected termination.'.format(data.log_prefix))
            return False
        return True

    # 10 second timeout
    rse_timeout = 10000

    continue_loop = True

    while continue_loop:
        # Check the thread status before we try to wake the agent
        continue_loop = __check_notifier(my_notifier)
        if not continue_loop:
            break

        if data.agent_engine.WakeSpecificAgent(agent_id, data.rse_engine, rse_timeout):
            # Agent is awake, so wait for the period before checking again
            start_time = int(round(time.time() * 1000))
            finish_time = start_time + rse_period
            while ((int(round(time.time() * 1000))) < finish_time) and continue_loop:
                # check the thread status every 1 second throughout the entire period wait
                continue_loop = __check_notifier(my_notifier)
                time.sleep(1)
        else:
            # Failed to wake the agent
            log.debug('{0:}: Failed to wake agent - {1:}'.format(data.log_prefix, agent_id))

    log.debug('{0:}: Terminating'.format(data.log_prefix))


class AgentDetailsNotAvailable(Exception):
    """
    Agent Details are not available
    """
    pass


class AgentConfigurationNotAvailable(Exception):
    """
    Agent Configuraiton is not available
    """
    pass


class AgentLogLevel(Command):
    """
    Object controlling the log levels for agents
    """
    def __init__(self, sslenabled, authenticator, apihost, api_version=1, project_id=None):
        super(self.__class__, self).__init__(sslenabled, apihost, '/')
        self.log = logging.getLogger(__name__)

        # save the ssl status for the various reinits done for each API call supported
        self.sslenabled = sslenabled
        self.authenticator = authenticator
        self.loglevel = {}

        if type(api_version) is int:
            self.api_version = api_version
        else:
            self.api_version = 1
        self.project_id = project_id

    def __del__(self):
        try:
            if len(self.loglevel):
                for machine_agent in self.loglevel.keys():
                    while self.HasLogLevels(machine_agent):
                        self.PopLogLevel(machine_agent)
        except:
            pass

    def GetLogLevel(self, machine_agent_id):
        """
        Retrieve the current log level for the agent from the API

        The returned value will be one of the following:
            Fatal
            Error
            Warn
            Info
            Debug
            Trace
            All
        """
        if self.api_version == 1:
            self.ReInit(self.sslenabled,
                        '/v1.0/agent/logging/{0}'.format(machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}'.format(self.api_version,
                                                      self.project_id,
                                                      machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id
        res = requests.get(self.Uri, headers=self.Headers)
        if res.status_code == 200:
            if self.api_version == 1:
                # the text will be data like "Warn" (with quotes) so remove the quotes.
                return res.text.replace('"', '')
            else:
                return res.json()['log_level']
        else:
            self.log.error('Unable to retrieve agent log level for machine agent id ' + str(machine_agent_id) + '. Server returned ' + str(res.status_code) + ': ' + res.text + ' Reason: ' + res.reason)
            return ''

    def SetLogLevel(self, machine_agent_id, level):
        """
        Set the log level for the agent via the API

        'level' must be one of the following:
            Fatal
            Error
            Warn
            Info
            Debug
            Trace
            All

        'level' may also be a numeric value inclusively between 1 and 7.
        """
        if self.api_version == 1:
            if not level in ('Fatal', 'Error', 'Warn', 'Info', 'Debug', 'Trace', 'All', 1, 2, 3, 4, 5, 6, 7):
                raise ValueError('Log Level (' + str(level) + ') is not valid.')

            self.ReInit(self.sslenabled, "/v1.0/agent/logging")
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            o = {}
            o['MachineAgentId'] = machine_agent_id

            levels = {
                'Fatal': 1,
                'Error': 2,
                'Warn': 3,
                'Info': 4,
                'Debug': 5,
                'Trace': 6,
                'All': 7
            }
            if level in levels:
                o['LoggingLevelid'] = levels[level]
            else:
                o['LoggingLevelid'] = level

            self.body = json.dumps(o)

            res = requests.put(self.Uri, headers=self.Headers, data=self.Body)
        else:
            # TODO: Need to rework this whole function
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}'.format(self.api_version,
                                                      self.project_id,
                                                      machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id
            o = {}
            o['op'] = 'replace'
            o['path'] = '/log_level'
            o['value'] = level.lower()
            l = []
            l.append(o)
            self.body = json.dumps(l)
            res = requests.patch(self.Uri, headers=self.Headers, data=self.Body)

        if res.status_code == 204:
            return True
        else:
            self.log.error('Unable to set the log level. Server returned ' + str(res.status_code) + ': ' + res.text + ' Reason: ' + res.reason)
            return False

    def PushLogLevel(self, machine_agent_id, level):
        """
        Save the current log level and set 'level' as the new log level.

        See SetLogLevel() for valid values of 'level'

        Note: Log Levels are stored as a Stack. Use PopLogLevel() to restore the log level to the value prior to calling PushLogLevel().
        """
        if not machine_agent_id in self.loglevel:
            self.loglevel[machine_agent_id] = list()
        current = self.GetLogLevel(machine_agent_id)
        self.loglevel[machine_agent_id].append(current)
        self.SetLogLevel(machine_agent_id, level)

    def HasLogLevels(self, machine_agent_id):
        """
        Returns whether or not there are any log levels for the given machine agent id
        """
        if machine_agent_id in self.loglevel:
            if len(self.loglevel[machine_agent_id]):
                return True
            else:
                return False
        else:
            return False

    def PopLogLevel(self, machine_agent_id):
        """
        Restore the previous log level if it exists.
        If not log level has been saved, then it does nothing.

        Note: Log Levels are stored as a Stack. Log Levels are added to the stack by calling PushLogLevel().
        """
        if machine_agent_id in self.loglevel:
            if len(self.loglevel[machine_agent_id]):
                index = len(self.loglevel[machine_agent_id]) - 1
                level = self.loglevel[machine_agent_id][index]
                if self.SetLogLevel(machine_agent_id, level):
                    self.loglevel[machine_agent_id].pop(index)
                    self.log.info('Restored Machine Agent Id (' + str(machine_agent_id) + ') Log Level to ' + level)
                else:
                    self.log.error('Error while resetting the log level for Machine Agent Id (' + str(machine_agent_id) + ') to ' + level)

            else:
                self.log.error('Machine Agent Id (' + str(machine_agent_id) + ') is already at the root log level. Nothing left to pop.')
        else:
            self.log.error('Machine Agent Id (' + str(machine_agent_id) + ') does not have any stacked log levels')


class AgentDetails(object):
    """
    Object describing a given Agent instance described by the Agent Details API Endpoint
    """

    def __init__(self, details, version=1):

        # TODO: Replace this verification and use JSON Schema
        self.version = version
        if self.version == 1:
            # Verify the details are at least what we expect before doing anything else
            for prop in ('MachineAgentId', 'AgentVersion', 'Architecture', 'Flavor', 'BackupVaultSize', 'CleanupAllowed', 'Datacenter', 'IPAddress', 'IsDisabled', 'IsEncrypted', 'MachineName', 'OperatingSystem', 'OperatingSystemVersion', 'PublicKey', 'Status', 'TimeOfLastSuccessfulBackup', 'UseServiceNet', 'HostServerId'):
                x = details[prop]
        # TODO: Add JSON Schema validation for API v2

        # Some cached data needed
        self._details = details

    @property
    def agent_id(self):
        """
        Agent ID
        """
        if self.version == 1:
            return self._details['MachineAgentId']
        else:
            return self._details['id']

    @property
    def AgentVersion(self):
        """
        Agent Version
        """
        if self.version == 1:
            return self._details['AgentVersion']
        else:
            return self._details['version']

    @property
    def Architecture(self):
        """
        System Architecture
        """
        if self.version == 1:
            return self._details['Architecture']
        else:
            return self._details['host']['os']['architecture']

    @property
    def Flavor(self):
        """
        System Flavor
        """
        if self.version == 1:
            return self._details['Flavor']
        else:
            return self._details['host']['flavor']

    @property
    def BackupVaultSize(self):
        """
        Current size of the Backup Vault
        """
        # TODO: v2 does not have Backup Vault Size
        return self._details['BackupVaultSize']

    @property
    def CleanupAllowed(self):
        """
        Can Cleanup the Vault?
        """
        # TODO: v2 does not have CleanupAllowed
        return self._details['CleanupAllowed']

    @property
    def Datacenter(self):
        """
        Which Datacenter does the system live in?
        """
        if self.version == 1:
            return self._details['Datacenter']
        else:
            return self._details['host']['region']

    @property
    def IPAddress(self):
        """
        IP Address the agent registered with
        """
        if self.version == 1:
            return self._details['IPAddress']
        else:
            return next(address for address in
                        self._details['host']['addresses']
                        if address['version'] == 4)['addr']

    @property
    def IsDisabled(self):
        """
        Is the Agent Disabled?
        """
        if self.version == 1:
            return self._details['IsDisabled']
        else:
            return not self._details['enabled']

    @property
    def IsEnabled(self):
        """
        Is the Agent Enabled?
        """
        return not self.IsDisabled

    @property
    def IsEncrypted(self):
        """
        Are the backups encrypted?
        """
        if self.version == 1:
            return self._details['IsEncrypted']
        else:
            return self._details['vault']['encrypted']

    @property
    def MachineName(self):
        """
        System Name as registered with Cloud Servers (Nova)
        """
        if self.version == 1:
            return self._details['MachineName']
        else:
            return self._details['name']

    @property
    def OperatingSystem(self):
        """
        System Operating System
        """
        if self.version == 1:
            return self._details['OperatingSystem']
        else:
            return self._details['host']['os']['name']

    @property
    def OperatingSystemVersion(self):
        """
        System Operating System Version
        """
        if self.version == 1:
            return self._details['OperatingSystemVersion']
        else:
            return self._details['host']['os']['version']

    @property
    def PublicKey(self):
        """
        Public Key for encrypted backups
        """
        if self.version == 1:
            return self._details['PublicKey']
        else:
            # TODO: Content of rsa_public_key is a different from that of PublicKey. Is this function being used?
            return self._details['rsa_public_key']

    @property
    def Status(self):
        """
        Agent Status
        """
        if self.version == 1:
            return self._details['Status']
        # TODO: API v2 provides http://docs.cloudbackupapi.apiary.io/#reference/agents/v2agentsidstatus/get-an-agent's-status
        #       to get a real-time status of the agent

    @property
    def TimeOfLastSuccessfulBackup(self):
        """
        When was the agent last succcessful with its backup?
        """
        if self.version == 1:
            return self._details['TimeOfLastSuccessfulBackup']
        # TODO: API v2 provides this a little differently:
        #       First call http://docs.cloudbackupapi.apiary.io/#reference/configurations/v2configurationsid/get-details-about-a-configuration
        #       to get the last time a given configuration was backed up, then retrieve the details via
        #       http://docs.cloudbackupapi.apiary.io/#reference/backups/v2backupsid/get-details-about-a-backup to find the time of that backup

    @property
    def DateTimeOfLastSuccessfulBackup(self):
        """
        When was the agent last succcessful with its backup?
        """
        a = self.TimeOfLastSuccessfulBackup.split('(')
        b = a[1].split(')')
        unix_epoch = b[0]
        return datetime.datetime.utcfromtimestamp(float(unix_epoch) / 1000.0)

    @property
    def UseServiceNet(self):
        """
        Use RAX ServiceNet?
        """
        if self.version == 1:
            return self._details['UseServiceNet']
        else:
            return self._details['vault']['use_internal']

    @property
    def HostServerId(self):
        """
        System Host Server Identifier for Cloud Servers (Nova)
        """
        if self.version == 1:
            return self._details['HostServerId']
        else:
            return self._details['host']['machine']['id']


class AgentConfiguration(object):
    """
    Object describing the various Agent configurations
    """

    def __init__(self, configuration, version=1):

        # TODO: Replace this verification and use JSON Schema
        self.version = version
        if self.version == 1:
            # Verify the configurations are at least what we expect before doing anything else
            for prop in ('Volumes', 'SystemPreferences', 'UserPreferences', 'BackupConfigurations'):
                x = configuration[prop]
        # TODO: Add JSON Schema validation for API v2

        self.log = logging.getLogger(__name__)

        # some cached data needed
        self._configuration = configuration

    # Volumes[]
    # -> DataServices
    # -> Uri
    # -> FailoverUri
    # -> EncryptionEnabled
    # -> Password
    # -> NetworkDrives
    # -> BackupVaultId
    @property
    def Volumes(self):
        if self.version == 1:
            return self._configuration['Volumes']
        else:
            # TODO: This is not a one to one mapping. The key/values are different
            return self._configuration['vaults']

    # SystemPreferences          See SystemPreferences
    # ->RateLimit
    # ->AutoUpdate
    #   --> Enabled
    #   --> LatestVersion
    # -> Environment
    #   --> MinimumDiskSpaceMb
    #     ---> Backup           See MinimumBackupDiskSpaceMb()
    #     ---> Restore          See MinimumRestoreDiskSpaceMb()
    #     ---> Cleanup          See MinimumCleanupDiskSpaceMb()
    # -> Logging
    #   --> Level               See ConfigLogLevel()
    @property
    def SystemPreferences(self):
        if self.version == 1:
            return self._configuration['SystemPreferences']
        else:
            # TODO: This is not a one to one mapping. The key/values are different
            return self._configuration['system_preferences']

    @property
    def ConfigLogLevel(self):
        if self.version == 1:
            return self.SystemPreferences['Logging']['Level']
        else:
            return self._configuration['system_preferences']['logging']['level']

    @property
    def MinimumBackupDiskSpaceMb(self):
        if self.version == 1:
            return self.SystemPreferences['Environment']['MinimumDiskSpaceMb']['Backup']
        else:
            return self._configuration['system_preferences']['environment']['minimum_disk_space_mb']['backup']

    @property
    def MinimumRestoreDiskSpaceMb(self):
        if self.version == 1:
            return self.SystemPreferences['Environment']['MinimumDiskSpaceMb']['Restore']
        else:
            return self._configuration['system_preferences']['environment']['minimum_disk_space_mb']['restore']

    @property
    def MinimumCleanupDiskSpaceMb(self):
        if self.version == 1:
            return self.SystemPreferences['Environment']['MinimumDiskSpaceMb']['Cleanup']
        else:
            return self._configuration['system_preferences']['environment']['minimum_disk_space_mb']['cleanup']

    # UserPreferences
    # -> CacheDirectory
    # -> ThrottleBandwidth
    @property
    def UserPreferences(self):
        if self.version == 1:
            return self._configuration['UserPreferences']
        else:
            # TODO: Need to look into the equivalent value/object
            return None

    # BackupConfigurations[]   See GetBackupConfigurationById(), GetBackupConfigurationByName()
    # -> BackupPrescript
    # -> BackupPostscript
    # -> Id                   See GetBackupIds(), GetBackupIdNameMap()
    # -> VolumeUri
    # -> VolumeFailoverUri
    # -> Name                 See GetBackupNames(), GetBackupNameIdMap()
    # -> IsEnabled
    # -> DaysToKeepOldFileVersions
    # -> KeepOldFileVersionsIndefinitely
    # -> Schedules[]
    #   --> Start
    #   --> End
    #   --> InitialScheduledTime
    #   --> Frequency
    #   --> TimeOfDay
    #   --> DayOfWeek
    #   --> HourlyInterval
    #   --> IsDST
    #   --> Offset
    # -> Inclusions[]
    #   --> Pattern
    #   --> Type
    #   --> Module
    #   --> Args
    # -> Exclusions[]
    #   --> Pattern
    #   --> Type
    #   --> Module
    #   --> Args
    @property
    def BackupConfigurations(self):
        if self.version == 1:
            return self._configuration['BackupConfigurations']
        else:
            # TODO: This is not a one to one mapping. The key/values are different
            return self._configuration['configurations']

    # Rse                  See GetRse()
    # -> Channel          See GetRseChannel()
    # -> HostName         See GetRseHost()
    # -> Polling          See GetRsePollingConfig()
    #   --> Interval
    #     ---> Idle
    #     ---> Active
    #     ---> RealTime
    #   --> Timeout
    #     ---> Idle
    #     ---> Active
    #     ---> RealTime
    # -> Heartbeat        See GetRseHeartbeatConfig()
    #   --> Interval
    #     ---> Idle
    #     ---> Active
    #     ---> RealTime
    #   --> Timeout
    #     ---> Idle
    #     ---> Active
    #     ---> RealTime
    @property
    def Rse(self):
        if self.version == 1:
            return self.SystemPreferences['Rse']
        else:
            # TODO: This is not a one to one mapping. The key/values are different
            return self._configuration['system_preferences']['events']['rse']

    @property
    def RseChannel(self):
        if self.version == 1:
            return self.Rse['Channel']
        else:
            return self._configuration['system_preferences']['events']['rse']['channel']

    @property
    def RseHost(self):
        if self.version == 1:
            return self.Rse['HostName']
        else:
            return self._configuration['system_preferences']['events']['rse']['host']

    @property
    def RsePollingConfig(self):
        if self.version == 1:
            return self.Rse['Polling']
        else:
            return self._configuration['system_preferences']['events']['rse']['polling']

    @property
    def RseHeartbeatConfig(self):
        if self.version == 1:
            return self.Rse['Heartbeat']
        else:
            return self._configuration['system_preferences']['events']['rse']['heartbeat']

    def GetBackupIds(self):
        """
        Retrieve the list of Backup Configuration Ids for the agent as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            backup_id = 'Id'
        else:
            backup_id = 'id'
        backupids = set()
        for backupconfig in self.BackupConfigurations:
            backupids.add(backupconfig[backup_id])
        return backupids

    def GetBackupNames(self):
        """
        Retrieve the list of Backup Configuration Names for the agent as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            backup_name = 'Name'
        else:
            backup_name = 'name'
        backupnames = set()
        for backupconfig in self.BackupConfigurations:
            backupnames.add(backupconfig[backup_name])
        return backupnames

    def GetBackupNameIdMap(self):
        """
        Retrieve the list of Backup Configuration Names for the agent as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            backup_name = 'Name'
            backup_id = 'Id'
        else:
            backup_name = 'name'
            backup_id = 'id'
        backupnamemap = {}
        for backupconfig in self.BackupConfigurations:
            backupnamemap[backupconfig[backup_name]] = backupconfig[backup_id]
        return backupnamemap

    def GetBackupIdNameMap(self):
        """
        Retrieve the list of Backup Configuration Names for the agent as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            backup_name = 'Name'
            backup_id = 'Id'
        else:
            backup_name = 'name'
            backup_id = 'id'
        backupidmap = {}
        for backupconfig in self.BackupConfigurations:
            backupidmap[backupconfig[backup_id]] = backupconfig[backup_name]
        return backupidmap

    def GetBackupIdFromName(self, backup_name):
        """
        Translate the backup name into a backup id based on the agent data reported by GetAgentConfiguration()

        Note: It would be more performant to simply retrieve the configuration by the name instead of doing the translation
        """
        backupnamemap = self.GetBackupNameIdMap()
        return backupnamemap[backup_name]

    def GetBackupNameFromId(self, backup_id):
        """
        Translate the backup id into a backup name based on the agent data reported by GetAgentConfiguration()

        Note: It would be more performant to simply retrieve the configuration by the id instead of doing the translation
        """
        backupidmap = self.GetBackupIdNameMap()
        return backupidmap[backup_id]

    def GetBackupConfigurationById(self, backup_id):
        """
        Retrieve the entire backup configuration for the agent given a backup id, data as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            b_id = 'Id'
        else:
            b_id = 'id'
        return next((backupconfig for backupconfig in self.BackupConfigurations if backupconfig[b_id] == backup_id), {})

    def GetBackupConfigurationByName(self, backup_name):
        """
        Retrieve the entire backup configuration for the agent given a backup id, data as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            b_name = 'Name'
        else:
            b_name = 'name'
        return next((backupconfig for backupconfig in self.BackupConfigurations if backupconfig[b_name] == backup_name), {})

    def GetVaultDbContainer(self, backup_name=None):
        """
        Retrieve the URI for the VaultDB, data as reported by GetAgentConfiguration()
        """
        if self.version == 1:
            container = None
            if backup_name is not None:
                backupconfig = self.GetBackupConfigurationByName(backup_name)
                container = backupconfig['VolumeUri']
            else:
                container = self.Volumes[0]['Uri']
            self.log.debug('VaultDB Container: ' + container)
            return container[6:]
        else:
            vault_info = self._configuration['vaults'][0]
            if vault_info['use_internal']:
                vault_url = next(url for url in vault_info['links']
                                 if url['rel'] == 'internalURL')['href']
            else:
                vault_url = next(url for url in vault_info['links']
                                 if url['rel'] == 'publicURL')['href']
            self.log.debug('VaultDB Container: ' + vault_url)
            # strip the https:// section
            return vault_url[8:]


    def GetVaultDbPath(self, backup_name=None):
        """
        Retrieve the URI for the VaultDB, data as reported by GetAgentConfiguration()
        """
        try:
            if self.version == 1:
                vaultvolume = {}
                if backup_name is not None:
                    backupconfig = self.GetBackupConfigurationByName(backup_name)
                    volumeuri = backupconfig['VolumeUri']
                    vaultvolume = {}
                    # As there may be numerous volumes we match it up against the backup configuration we are looking for
                    # Don't know if there is a better way or not...but this will work for now
                    for volume in self.Volumes:
                        if volume['Uri'] == volumeuri:
                            vaultvolume = volume
                else:
                    vaultvolume = self.Volumes[0]

                vaultdburi = 'BACKUPS/v2.0/' + vaultvolume['BackupVaultId']
                self.log.debug('VaultDB Path: ' + vaultdburi)
                return vaultdburi
            else:
                backupconfig = self.GetBackupConfigurationByName(backup_name)
                vault_id = backupconfig['vault_id']
                vaultdburi = 'BACKUPS/v2.0/{0}'.format(vault_id)
                self.log.debug('VaultDB Path: ' + vaultdburi)
                return vaultdburi
        except LookupError:
            self.log.error('Unable to access the Volume URI. Did GetAgentConfiguration get called first?')
            return ''

    def GetBundlePath(self, backup_name, bundle_id):
        """
        Retrieve the URI for the Bundle

        Depends on GetAgentConfiguration() to have already been called
        """
        try:
            if self.version == 1:
                backupconfig = self.GetBackupConfigurationByName(backup_name)
                volumeuri = backupconfig['VolumeUri']
                vaultvolume = {}
                # As there may be numerous volumes we match it up against the backup configuration we are looking for
                # Don't know if there is a better way or not...but this will work for now
                for volume in self.Volumes:
                    if volume['Uri'] == volumeuri:
                        vaultvolume = volume
                vaultdburi = 'BACKUPS/v2.0/' + vaultvolume['BackupVaultId'] + '/BUNDLES/' + '{0:010}'.format(bundle_id)
                self.log.debug('VaultDB Path: ' + vaultdburi)
                return vaultdburi
            else:
                vault_db_url = self.GetVaultDbPath(backup_name)
                vaultdburi = vault_db_url + '/BUNDLES/' + \
                        '{0:010}'.format(bundle_id)
                self.log.debug('VaultDB Path: ' + vaultdburi)
                return vaultdburi
        except LookupError:
            self.log.error('Unable to access the Volume URI. Did GetAgentConfiguration get called first?')
            return ''


class Agents(Command):
    """
    Object defining HTTP REST API calls for interactiving with the Rackspace Cloud Backup Agent
    Presently supports the RAX v1.0 API
    """

    def __init__(self, sslenabled, authenticator, apihost, api_version=1, project_id=None):
        """
        Initialize the Agent access
          sslenabled - True if using HTTPS; otherwise False
          authenticator - instance of cloudbackup.client.auth.Authentication to use
          apihost - server to use for API calls
          api_version - version of the API
          project_id - Project Id used by API v2
        """
        super(self.__class__, self).__init__(sslenabled, apihost, '/')
        self.log = logging.getLogger(__name__)
        # save the ssl status for the various reinits done for each API call supported
        self.sslenabled = sslenabled
        self.authenticator = authenticator
        # Some cached data needed, set to invalid values by default
        self.agents = {}
        self.configurations = {}
        self.o = {}
        self.snapshot_id = -1
        self.wake_agent_threads = []
        self.loglevel = AgentLogLevel(sslenabled, authenticator, apihost,
                                      api_version, project_id)

        if type(api_version) is int:
            self.api_version = api_version
        else:
            self.api_version = 1

        self.project_id = project_id

    def __del__(self):
        del self.loglevel

        # Loop through and tell all threads to terminate
        # Do not wait for them to terminate here so that all get the
        # message in a timely manner
        for a_thread in self.wake_agent_threads:
            self.log.debug('Telling RSE Wakeup Thread {0:} to terminate'.format(a_thread['id']))
            a_thread['terminator'].set()

        # Now repeat and wait for them to terminate
        for a_thread in self.wake_agent_threads:
            self.log.debug('Waiting for RSE Wakeup Thread {0:} to rejoin'.format(a_thread['id']))
            a_thread['thread'].join()

    def WakeAgents(self):
        """
        Using the API move all agents to active poll mode

        Note: This may require up to 60 seconds for the agents to respond.
        """
        if self.api_version == 1:
            self.ReInit(self.sslenabled, "/v1.0/user/wakeupagents")
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.log.debug('headers: %s', self.Headers)
            res = requests.post(self.Uri, headers=self.Headers)
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/events'.format(self.api_version,
                                                  self.project_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id
            self.o = {}
            self.o['event'] = 'agent_activate'
            self.o['mode'] = 'active'
            self.body = json.dumps(self.o)
            self.log.debug('headers: %s', self.Headers)
            res = requests.post(self.Uri, headers=self.Headers, data=self.Body)
        self.log.debug('Wake Agent: code = {0:}, reason = {1:}'.format(res.status_code, res.reason))
        return res.status_code

    def WakeSpecificAgent(self, machine_agent_id, rse, timeoutMilliseconds, keep_agent_awake=False, wake_period=None):
        """
        Using the API to move all agents to active poll mode and then check that a specific agent is polling.
          machine_agent_id  - agent id for the specific agent to look for
          rse - instance of the cloudbackup.client.rse.Rse class to use for listening to RSE
          timeoutMilliseconds - maximum time to check RSE for the data
          keep_agent_awake - whether or not to start a thread to keep posting the wake agent
          wake_period - period between wake agent calls, should be less than the timeout interval for the current state of the agent
            normally 70 seconds should be fine. If set to None, use the Real-Time Timeout as a basis and set appropriately defaulting to 70 if too small
        """
        # For up to timeoutMilliseconds try to wake all the agents on the account in use
        start_time = int(round(time.time() * 1000))
        finish_time = start_time + timeoutMilliseconds
        wokeall = False
        wakeup_status_code = 0
        while ((int(round(time.time() * 1000))) < finish_time):
            wakeup_status_code = self.WakeAgents()
            if self.api_version == 1:
                valid_code = 200
            else:
                valid_code = 202
            if wakeup_status_code == valid_code:
                wokeall = True
                break
        if wokeall:
            # For up to timeoutMilleseconds look for the specified agent's heart beat
            start_time = int(round(time.time() * 1000))
            finish_time = start_time + timeoutMilliseconds
            woke_agent = False
            while ((int(round(time.time() * 1000))) < finish_time):
                if rse.MonitorForHeartBeat(machine_agent_id):
                    woke_agent = True
                    break
            if not woke_agent:
                # Unable to find the agent's heart beat within the timeout period
                self.log.error('Unable to locate agent id (' + str(machine_agent_id) + ') in RSE Heartbeats')
            if woke_agent:
                if keep_agent_awake:
                    if wake_period is None:
                        rse_heartbeat_config = self.GetRseHeartbeatConfig(machine_agent_id)
                        self.log.debug('Rse config: {0:}'.format(rse_heartbeat_config))
                        if self.api_version == 1:
                            wake_period = (rse_heartbeat_config['Timeout']
                                           ['RealTime'] / 1000)
                        else:
                            wake_period = (rse_heartbeat_config['timeout_ms']
                                           ['real_time'] / 1000)
                        # create a buffer
                        if wake_period > 6:
                            wake_period = wake_period - 5
                        elif wake_period > 2:
                            wake_period = wake_period - 1
                        else:
                            # if it's too small then default to a reasonable time frame
                            # UX uses approximately 70 seconds
                            wake_period = 70

                    self.KeepAgentAwake(machine_agent_id, rse, wake_period)
            return woke_agent
        else:
            # Unable to use the API to wake the agents within the timeout period
            self.log.error('Unable to wake all agents. Status Code = ' + str(wakeup_status_code))
            return False

    def KeepAgentAwake(self, machine_agent_id, rse, period):
        """
        Start a thread that will periodically post Wake Agent and check that the agent is alive

        Parameters:
            machine_agent_id - machine agent id of the agent to monitor for heart beats
            rse - RSE instance configured for the agent
            period - period between posting wake agent messages

        Note: period is starts after a successful find of the agent heartbeat
        """
        wake_agent_thread = {}
        wake_agent_thread['id'] = machine_agent_id
        self.wake_agent_threads.append(wake_agent_thread)
        for a_thread in self.wake_agent_threads:
            if a_thread['id'] == machine_agent_id:
                self.log.debug('Starting RSE Wakeup Thread for agent: {0:}'.format(machine_agent_id))
                a_thread['terminator'] = threading.Event()
                a_thread['thread'] = threading.Thread(target=_keep_agent_wake_thread_fn,
                                                      kwargs={'user': self.authenticator.Username, 'apikey': self.authenticator.Apikey,
                                                              'rse_app': rse.rsedata.app, 'rse_version': rse.rsedata.appVersion,
                                                              'rse_agentkey': rse.agentkey, 'rse_log': rse.rselogfile,
                                                              'rse_apihost': rse.apihost, 'rse_period': period,
                                                              'apihost': self.apihost, 'agent_id': machine_agent_id,
                                                              'my_notifier': wake_agent_thread['terminator'],
                                                               'api_version': self.api_version,
                                                               'project_id': self.project_id})
                a_thread['thread'].start()
                break

    def StopKeepAgentWake(self, machine_agent_id):
        """
        Stop the thread that is posting the wake agents and monitoring for the given machine agent id

        Parameters:
            machine_agent_id - the machine agent identifier that is being monitored for
        """
        for a_thread in self.wake_agent_threads:
            if a_thread['id'] == machine_agent_id:
                self.log.debug('Telling for RSE Wakeup Thread {0:} for agent {1:} to terminate'.format(a_thread['id'], machine_agent_id))
                a_thread['terminator'].set()
                self.log.debug('Waiting for RSE Wakeup Thread {0:} to rejoin'.format(a_thread['id']))
                a_thread['thread'].join()
                self.wake_agent_threads.remove(a_thread)
                break

    #
    # Agent Details
    #
    def GetAgentDetails(self, machine_agent_id):
        """
        Retrieve all the information regarding the specified Agent ID
        """
        self.agents = {}
        if self.api_version == 1:
            self.ReInit(self.sslenabled,
                        '/v1.0/agent/{0}'.format(machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}'.format(self.api_version,
                                                      self.project_id,
                                                      machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id
        res = requests.get(self.Uri, headers=self.Headers)
        if res.status_code == 200:
            self.log.debug('Agent Details(id: {0:}) - {1:}'.format(machine_agent_id, res.json()))
            self.agents[machine_agent_id] = AgentDetails(details=res.json(), version=self.api_version)
            return True
        else:
            self.log.error('Unable to retrieve agent details for agent id ' + str(machine_agent_id) + ' system return code ' + str(res.status_code) + ' reason = ' + res.reason)
            return False

    def GetAgentsFromApi(self):
        """
        Lookup the associated agents and return a list of their IDs
        """
        if self.api_version == 1:
            self.ReInit(self.sslenabled,
                        '/v1.0/user/agents')
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            res = requests.get(self.Uri, headers=self.Headers)
            if res.status_code == 200:
                result_list = []
                results = res.json()
                for agent in results:
                    result_list.append(agent['MachineAgentId'])
                return result_list

            else:
                self.log.error('Unable to retrieve agent list system return code ' + str(res.status_code) + ' reason = ' + res.reason)
                return []

        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents'.format(
                            self.api_version,
                            self.project_id
                        ))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id

            res = requests.get(self.Uri, headers=self.Headers)
            if res.status_code == 200:
                result_list = []
                results = res.json()
                for agent in results['agents']:
                    result_list.append(agent['id'])
                return result_list

            else:
                self.log.error('Unable to retrieve agent list system return code ' + str(res.status_code) + ' reason = ' + res.reason)
                return []


    @property
    def GetAgentIds(self):
        """
        Return a list of known agent ids for agents details retrieved by GetAgentDetails()
        """
        return self.agents.keys()

    def AgentDetails(self, machine_agent_id):
        """
        The AgentDetails object describing the agent with the given machine_agent_id
        """
        try:
            return self.agents[machine_agent_id]
        except LookupError:
            msg = 'Machine Agent Id ({0:}) not available. Did you call GetAgentDetails() for that agent?'.format(machine_agent_id)
            self.log.error(msg)
            raise AgentDetailsNotAvailable(msg)

    #
    # Agent Configurations
    #
    def GetAgentConfiguration(self, machine_agent_id):
        """
        Retrieve the Configuration for the given agent
        """
        if self.api_version == 1:
            self.ReInit(self.sslenabled,
                        '/v1.0/agent/configuration/{0}'.format(
                            machine_agent_id
                        )
            )
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}/configuration'.format(
                            self.api_version,
                            self.project_id,
                            machine_agent_id
                        )
            )
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id

        res = requests.get(self.Uri, headers=self.Headers)
        if res.status_code == 200:
            self.configurations[machine_agent_id] = AgentConfiguration(
                    configuration=res.json(), version=self.api_version)
            return True
        else:
            self.log.error('Unable to retrieve agent configuration for agent id ' + str(machine_agent_id) + '. Server returned ' + str(res.status_code) + ': ' + res.text + ' Reason: ' + res.reason)
            return False

    @property
    def AgentConfigurationIds(self):
        """
        Return a list of known agent ids for agent configurations retrieved by GetAgentConfiguration()
        """
        return self.configurations.keys()

    def AgentConfiguration(self, machine_agent_id):
        """
        Return the AgentConfiguration object containing the configuration for the agent with the given machine_agent_id
        """
        try:
            return self.configurations[machine_agent_id]
        except LookupError:
            msg = 'Machine Agent Id ({0:}) not available. Did you call GetAgentConfiguration() for that agent?'.format(machine_agent_id)
            self.log.error(msg)
            raise AgentConfigurationNotAvailable(msg)

    #
    # Agent Activity
    #
    def GetAgentLatestActivity(self, machine_agent_id):
        """
        Retrieve the current activities of the agent
        """
        # Get the agent configuration so that we know we can lookup the backup configs in order
        # to display a useful name about the activity to the user
        self.GetAgentConfiguration(machine_agent_id)
        agent_config = self.AgentConfiguration(machine_agent_id)

        if self.api_version == 1:
            self.ReInit(self.sslenabled,
                        '/v1.0/{0}/system/activity{1}'.format(
                            self.authenticator.AuthTenantId,
                            machine_agent_id
                        )
            )
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'

        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}/activities'.format(
                            self.api_version,
                            self.project_id,
                            machine_agent_id
                        )
            )
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.headers['X-Project-Id'] = self.project_id

        res = requests.get(self.Uri, headers=self.Headers)
        if res.status_code == 200:
            results = []

            if self.api_version == 1:
                for activity in res.json():
                    activity_name = ''
                    if activity['ParentId'] != 0:
                        try:
                            activity_name = '{0} - {1}'.format(
                                activity['Type'],
                                agent_config.GetBackupNameFromId(
                                    activity['ParentId']
                                )
                            )
                        except:
                            activity_name = '{0} - UNKNOWN({1})'.format(
                                activity['Type'],
                                activity['ParentId']
                            )
                    else:
                        activity_name = '{0} - {1}'.format(
                            activity['Type'],
                            activity['DisplayName']
                        )

                    results.append(
                        {
                        'id': activity['Id'],
                        'name': activity_name,
                        'type': activity['Type'],
                        'state': activity['CurrentState'],
                        'time': activity['TimeOfActivity']
                        }
                    )

            else:
                for activity in res.json()['activities']:
                    activity_name = ''
                    if 'configuration' in activity.keys():
                        try:
                            activity_name = '{0} - {1}'.format(
                                activity['type'],
                                agent_config.GetBackupNameFromId(
                                    activity['configuration']['id']
                                )
                            )
                        except:
                            activity_name = '{0} - UNKNOWN({1})'.format(
                                activity['type'],
                                activity['configuration']['id']
                            )
                    else:
                        activity_name = activity['type']

                    results.append(
                        {
                        'id': activity['id'],
                        'name': activity_name,
                        'type': activity['type'],
                        'state': activity['state'],
                        'time': activity['last_updated_time']
                        }
                    )

            return results

        else:
            self.log.error('Unable to retrieve latest agent activities for agent id ' + str(machine_agent_id) + '. Server returned ' + str(res.status_code) + ': ' + res.text + ' Reason: ' + res.reason)
            return []

    #
    # Agent Cleanup
    #
    def GetAllAgentsForHost(self, cloud_server_name=None, cloud_server_id=None, cloud_server_ips=None):
        """
        Retrieve a list (set) of agent identifiers for a given cloud server
            cloud_server_name - the name of the cloud server from Rackspace ControlPanel, also available via the bootstrap details and GetAgentDetails()

            Returns a set of dictionaries containing the following data:
                AgentVersion
                Architecture
                Flavor
                BackupVaultSize
                CleanupAllowed
                Datacenter
                IPAddress
                IsDisabled
                IsEncrypted
                MachineAgentId
                MachineName
                OperatingSystem
                OperatingSystemVersion
                PublicKey
                Status
                TimeOfLastSuccessfulBackup
                UseServiceNet
                HostServerId
        """
        if cloud_server_name is None and cloud_server_id is None and cloud_server_ips is None:
            raise ParameterError('Neither Cloud Server Name nor Cloud Server Id (HostServerId) nor Cloud Server IPs were specified. Unable to match a server.')

        if self.api_version == 1:
            self.ReInit(self.sslenabled, "/v1.0/user/agents")
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            res = requests.get(self.Uri, headers=self.Headers)
            if res.status_code == 200:
                agentlist = list()
                try:
                    usersagentlist = res.json()
                    for agent in usersagentlist:
                        self.log.debug('Agent: ' + str(agent))
                        if (cloud_server_id is not None and
                                'HostServerId' in agent):
                            self.log.debug(
                                    'Checking Id Match: {0:} == {1:}'
                                    .format(cloud_server_id,
                                            agent['HostServerId']))
                            if agent['HostServerId'] == cloud_server_id:
                                self.log.debug('Id Matched: Adding ' +
                                               str(agent))
                                agentlist.append(agent)
                                continue

                        if (cloud_server_name is not None and
                                'MachineName' in agent):
                            self.log.debug(
                                    'Checking Name Match: {0:} == {1:}'
                                    .format(cloud_server_name,
                                            agent['MachineName']))
                            if agent['MachineName'] == cloud_server_name:
                                self.log.debug('Name Matched: Adding ' +
                                               str(agent))
                                agentlist.append(agent)
                                continue

                        if (cloud_server_ips is not None and
                                'IPAddress' in agent):
                            self.log.debug(
                                    'Checking IP Match: {0:} in {1:}'
                                    .format(agent['IPAddress'],
                                            cloud_server_ips))
                            if agent['IPAddress'] in cloud_server_ips:
                                self.log.debug('IP Matched: Adding ' +
                                               str(agent))
                                agentlist.append(agent)
                                continue

                except LookupError:
                    self.log.error('Unable to retrieve all agents from the '
                                   'returned agent list')
                    self.log.error('system response: ' + res.text)
                    self.log.error('system reason: ' + res.reason)

                return agentlist
            else:
                if cloud_server_name is not None:
                    self.log.error('Unable to retrieve all agents for cloud '
                                   'server (name: ' + cloud_server_name +
                                   ') system return code ' +
                                   str(res.status_code))
                if cloud_server_id is not None:
                    self.log.error('Unable to retrieve all agents for cloud '
                                   'server (id: ' + cloud_server_id +
                                   ') system return code ' +
                                   str(res.status_code))
                self.log.error('system response: ' + res.text)
                self.log.error('system reason: ' + res.reason)
                return list()
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents'.format(self.api_version,
                                                  self.project_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            res = requests.get(self.Uri, headers=self.Headers)
            if res.status_code == 200:
                resp_body = res.json()
                agentlist = list()
                for entry in resp_body['agents']:
                    if cloud_server_id:
                        if entry['host']['machine']['id'] == cloud_server_id:
                            agentlist.append(entry)
                            continue

                    if cloud_server_name:
                        if entry['name'] == cloud_server_name:
                            agentlist.append(entry)
                            continue
                    if cloud_server_ips:
                        for address in entry['host']['addresses']:
                            if address['addr'] in cloud_server_ips:
                                agentlist.append(entry)
                                continue
                return agentlist
            else:
                if cloud_server_name is not None:
                    self.log.error('Unable to retrieve all agents for cloud '
                                   'server (name: ' + cloud_server_name +
                                   ') system return code ' +
                                   str(res.status_code))
                if cloud_server_id is not None:
                    self.log.error('Unable to retrieve all agents for cloud '
                                   'server (id: ' + cloud_server_id +
                                   ') system return code ' +
                                   str(res.status_code))
                self.log.error('system response: ' + res.text)
                self.log.error('system reason: ' + res.reason)
                return list()


    def RemoveAgent(self, machine_agent_id):
        """
        De-register the agent from the Rackspace Cloud Backup API
        """
        if self.api_version == 1:
            self.ReInit(self.sslenabled, '/v1.0/agent/delete')
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            self.o = {}
            self.o['MachineAgentId'] = machine_agent_id
            self.body = json.dumps(self.o)
            res = requests.post(self.Uri, headers=self.Headers, data=self.Body)
            if res.status_code == 204:
                self.log.info('Removed agent id ' + str(machine_agent_id))
                self.log.warn('Please restart the process to lookup this agent again as the agent id may have changed.')
                return True
            else:
                self.log.error('Unable to remove agent id ' + str(machine_agent_id) + ' system return code ' + str(res.status_code) + ' Reason: ' + res.reason)
                return False
        else:
            self.ReInit(self.sslenabled,
                        '/v{0}/{1}/agents/{2}'.format(self.api_version,
                                                      self.project_id,
                                                      machine_agent_id))
            self.headers['X-Auth-Token'] = self.authenticator.AuthToken
            self.headers['Content-Type'] = 'application/json; charset=utf-8'
            res = requests.delete(self.Uri, headers=self.Headers)
            if res.status_code == 204:
                self.log.info('Removed agent id ' + str(machine_agent_id))
                self.log.warn('Please restart the process to lookup this '
                              'agent again as the agent id may have changed.')
                return True
            else:
                self.log.error('Unable to remove agent id ' +
                               str(machine_agent_id) + ' system return code ' +
                               str(res.status_code) + ' Reason: ' + res.reason)
                return False

    def RemoveAllAgentsForHost(self, agent_list):
        """
        Remove all agents in the system registered to the same user using the same host server id
            host_server_id  - the host server id to remove agents from,
        """
        agents_removed = []
        for agent in agent_list:
            if self.RemoveAgent(agent['MachineAgentId']):
                agents_removed.append(agent['MachineAgentId'])
        return agents_removed

    def EnableDisableAgent(self, machine_agent_id, enabled=True):
        """
        Enable or Disable an agent
        """
        # TODO: update for v2 API
        self.ReInit(self.sslenabled, "/v1.0/agent/enable")
        self.headers['X-Auth-Token'] = self.authenticator.AuthToken
        self.headers['Content-Type'] = 'application/json; charset=utf-8'

        self.o = {}
        self.o['MachineAgentId'] = machine_agent_id
        self.o['Enable'] = enabled
        self.body = json.dumps(self.o)
        res = requests.post(self.Uri, headers=self.Headers, data=self.Body)
        if res.status_code == 204:
            # success
            self.log.info('Changed Agent Status - Machine Agent Id: {0:}, Enabled: {1:}'.format(machine_agent_id, enabled))
            return True

        elif res.status_code == 401:
            # bad credentials
            self.log.warn('Invalid AuthToken')
            return False

        elif res.status_code == 403:
            # no permissions
            self.log.warn('User does not have permission to enable/disable this system.')
            return False

        else:
            # other issue - 400, 500, 503, or something else
            self.log.error('Error (code: {0:}): {1:}'.format(res.status_code, res.text))
            return False
