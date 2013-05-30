import requests


_int_to_status = {
    0: 'Creating',
    1: 'Queued',
    2: 'InProgress',
    3: 'Completed',
    4: 'Stopped',
    5: 'Failed',
    6: 'StartRequested',
    7: 'StopRequested',
    8: 'CompletedWithErrors',
    9: 'Preparing'
}

BUSY_STATUS = ('StartRequested', 'Creating', 'InProgress',
               'StopRequested', 'Queued', 'Preparing')
DONE_STATUS = ('Completed', 'CompletedWithErrors', 'Stopped',
               'Skipped', 'Failed', 'Missed')

_predicates = {
    "backup_history": lambda j: j['Type'] == 'Backup' and not is_running(j),
    "restore_history": lambda j: j['Type'] == 'Restore' and not is_running(j),
    "active_backups": lambda j: j['Type'] == 'Backup' and is_running(j),
    "active_restores": lambda j: j['Type'] == 'Restore' and is_running(j),
    "active": lambda j: is_running(j)
}


def is_running(job):
    return job['CurrentState'] in BUSY_STATUS


def jobs(host, key, predicate, agent_id=None):
    url = ('{0}/{1}'.format(host, 'activity') if not agent_id else
           '{0}/{1}/{2}/{3}'.format(host, 'system', 'activity', agent_id))
    headers = {'x-auth-token': key}
    resp = requests.get(url, headers=headers, verify=False)
    resp.raise_for_status()
    return [b for b in resp.json() if predicate(b)]


def any_running(host, key, agent_id=None):
    return len(jobs(host, key, _predicates['active'], agent_id)) > 0


def backup_history(host, key, agent_id=None):
    return jobs(host, key, _predicates['backup_history'], agent_id)


def restore_history(host, key, agent_id=None):
    return jobs(host, key, _predicates['restore_history'], agent_id)


def active_backups(host, key, agent_id=None):
    return jobs(host, key, _predicates['active_backups'], agent_id)


def active_restores(host, key, agent_id=None):
    return jobs(host, key, _predicates['active_restores'], agent_id)
