import json
import logging
import os
import re
import shutil
import shlex
import sys
import traceback
import time

import pdg
from pdg.scheduler import PyScheduler, evaluateParamOr, convertEnvMapToUTF8
from pdg.job.callbackserver import CallbackServerMixin
from pdg.utils import TickTimer, expand_vars
from pdgjob import pdgcmd

import af
import afcommon
import services.service

logging.basicConfig(level = logging.DEBUG)
logger = logging.getLogger(__name__)


class AfanasyScheduler(CallbackServerMixin, PyScheduler):
    """
    Scheduler implementation that interfaces with a Afanasy farm instance.
    """
    def __init__(self, scheduler, name):
        PyScheduler.__init__(self, scheduler, name)
        CallbackServerMixin.__init__(self, False)
        self.active_jobs = {}
        self.tick_timer = None
        self.custom_port_range = None
        self.cook_id = '0'

    @classmethod
    def templateName(cls):
        return 'afanasyscheduler'

    @classmethod
    def templateBody(cls):
        return json.dumps({
            "name": "afanasyscheduler",
            "parameters" : [
                {
                    "name" : "address",
                    "label" : "Afanasy Server Address",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "callbackportrange",
                    "label" : "Callback Port Range",
                    "type" : "Integer",
                    "size" : 2,
                },
                {
                    "name" : "overrideportrange",
                    "label" : "Enable callbackportrange",
                    "type" : "Integer",
                    "size" : 1,
                },
                {
                    "name" : "localsharedroot",
                    "label" : "Local Root Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "overrideremoterootpath",
                    "label" : "",
                    "type" : "Integer",
                    "size" : 1,
                },
                {
                    "name" : "remotesharedroot",
                    "label" : "Farm Root Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "hfs_linux_path",
                    "label" : "Linux HFS Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "hfs_macosx_path",
                    "label" : "macOS HFS Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "hfs_windows_path",
                    "label" : "Windows HFS Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "hfspathuniversal",
                    "label" : "Universal HFS Path",
                    "type" : "String",
                    "size" : 1,
                },
                {
                    "name" : "useuniversalhfs",
                    "label" : "Use Universal HFS",
                    "type" : "Integer",
                    "size" : 1,
                }
            ]
        })

    def __del__(self):
        if self.tick_timer:
            self.tick_timer.cancel()

    def _localsharedroot(self):
        """
        returns the local path to sharedroot, possibly by contacting the 
        server.  Returns None on failure.
        """
        localsharedroot = self["localsharedroot"].evaluateString()
        if not os.path.exists(localsharedroot):
        	raise RuntimeError('localsharedroot file path not found: ' + localsharedroot)
        return localsharedroot

    def _updateWorkingDir(self):
        """
        returns the full path to working dir, rooted with env var which can be interpreted by slave on farm.
        Local working dir is set as user provided.
        """
        workingbase = self["pdg_workingdir"].evaluateString()
        if os.path.isabs(workingbase):
            raise RuntimeError("Relative Job Directory \'" + workingbase + "\' must be relative path!")

        local_wd = os.path.normpath(self["localsharedroot"].evaluateString() + "/" + workingbase)
        local_wd = local_wd.replace("\\", "/")
        if self["overrideremoterootpath"].evaluateInt() == 0:
            remote_wd = local_wd
        else:
            remote_wd = '{}/{}'.format(self['remotesharedroot'].evaluateString(), workingbase)
        self.setWorkingDir(local_wd, remote_wd)

    def _getHFSPath(self, platform):
        pth = None
        if self["useuniversalhfs"].evaluateInt() > 0:
            pth = self["hfspathuniversal"].evaluateString()
        elif platform.startswith('win'):
            pth = self["hfs_windows_path"].evaluateString()
        elif platform.startswith('darwin') or platform.startswith('mac'):
            pth = self["hfs_macosx_path"].evaluateString()
        elif platform.startswith('linux'):
            pth = self["hfs_linux_path"].evaluateString()
        return pth

    def pythonBin(self, platform):
        """
        [virtual] Returns the path to a python executable.  This executable
        will be used to execute generic python and is substituted in commands 
        with the __PDG_PYTHON__ token. 
        
        platform Is an identifier with the same rules as python's sys.platform.
                 (should be 'linux*' | 'darwin' | 'win*')
        local    True means returns the absolute path on the local file system.
        """
        # local python can be overriden with PDG_PYTHON env var
        val = 'python'
        if platform.startswith('win'):
            val = '$HFS/python27/python.exe'
        elif platform.startswith('linux'):
            val = '$HFS/python/bin/python'
        val = os.environ.get('PDG_PYTHON') or os.path.expandvars(val)
        return val

    def hythonBin(self, platform):
        """
        [virtual] Returns the path to a hython executable.  This executable
        will be used to execute hython and is substituted in commands 
        with the __PDG_HYTHON__ token. 
        
        platform Is an identifier with the same rules as python's sys.platform.
                 (should be 'linux*' | 'darwin' | 'win*')
        """
        # local hython can be overriden with PDG_HYTHON env var
        val = 'hython'
        if platform.startswith('win'):
            val = '$HFS/bin/hython.exe'
        elif platform.startswith('linux') or platform.startswith('darwin'):
            val = '$HFS/bin/hython'
        val = os.environ.get('PDG_HYTHON') or os.path.expandvars(val)
        return val


    def workItemResultServerAddr(self):
        return self['address'].evaluateString()  

    def onSchedule(self, work_item):
        """
        onSchedule(self, pdg.PyWorkItem) -> pdg.SchedulerResult

        Schedules the work item, e.g. submits a job to Tractor to perform
        work described in the work item. Returns pdg.scheduleResult.Succeeded
        on success, and pdg.scheduleResult.Failed on failures. Never cooks
        the work item directly, and thus does not return
        pdg.scheduleResult.CookSucceeded unless command is empty
        """
        if len(work_item.command) == 0:
            return pdg.scheduleResult.CookSucceeded
        try:
            item_name = work_item.name
            item_id = work_item.id
            node = work_item.node
            node_name = node.name
            item_command = work_item.command

            logger.debug('onSchedule input: {} {} {}'.format(node_name, item_name, item_command))

            job_name = 'workitem_{}'.format(node_name)
            task_name = item_name

            temp_dir = self.tempDir(False)
            work_dir = self.workingDir(False)
            script_dir = self.scriptDir(False)

            item_command = item_command.replace("__PDG_ITEM_NAME__", item_name)
            item_command = item_command.replace("__PDG_SHARED_TEMP__", temp_dir)
            item_command = item_command.replace("__PDG_TEMP__", temp_dir)
            item_command = item_command.replace("__PDG_DIR__", work_dir)
            item_command = item_command.replace("__PDG_SCRIPTDIR__", script_dir)
            item_command = item_command.replace("__PDG_RESULT_SERVER__", self.workItemResultServerAddr())
            item_command = item_command.replace("__PDG_PYTHON__", self.pythonBin(sys.platform))
            item_command = item_command.replace("__PDG_HYTHON__", self.hythonBin(sys.platform))

            cmd_argv = ' '.join(shlex.split(item_command))

            if len(cmd_argv) < 2:
                logger.error('Could not shelx command: ' + item_command)
                return pdg.scheduleResult.Succeeded

            # Ensure directories exist and serialize the work item
            self.createJobDirsAndSerializeWorkItems(work_item)

            # Path PDGcmd file
            src_file = os.environ.get('HOUDINI_USER_PREF_DIR') + '/pdg/types/pdgcmd.py'
            dest_file = script_dir + '/' + 'pdgcmd.py'
            shutil.copy(src_file, dest_file)

            # Create Job
            job = af.Job(job_name)

            # Job Parameters
            job.setBranch(self['job_branch'].evaluateString())
            job.setDependMask(self['depend_mask'].evaluateString())
            job.setDependMaskGlobal(self['depend_mask_global'].evaluateString())
            job.setPriority(self['priority'].evaluateInt())
            job.setMaxRunningTasks(self['max_runtasks'].evaluateInt())
            job.setMaxRunTasksPerHost(self['maxperhost'].evaluateInt())
            job.setHostsMask(self['hosts_mask'].evaluateString())
            job.setHostsMaskExclude(self['hosts_mask_exclude'].evaluateString())

            service = 'generic'
            parser = 'generic'

            # Create a block with provided name and service type
            block = af.Block(job_name, service)
            block.setService(service)
            block.setParser(parser)
            block.setCapacity(self['capacity'].evaluateInt())
            #block.setVariableCapacity(self['capacity_coefficient1'].evaluateInt(), self['capacity_coefficient2'].evaluateInt())
            block.setTaskMinRunTime(self['minruntime'].evaluateInt())
            block.setTaskMaxRunTime(self['maxruntime'].evaluateInt() * 3600)

            # Set Enviroment Task Variables
            block.setEnv('PDG_RESULT_SERVER', str(self.workItemResultServerAddr()))
            block.setEnv('PDG_ITEM_NAME', str('workitem_{}'.format(item_name)))
            block.setEnv('PDG_DIR', str(work_dir))
            block.setEnv('PDG_TEMP', str(temp_dir))
            block.setEnv('PDG_SHARED_TEMP', str(temp_dir))
            block.setEnv('PDG_INDEX', str(work_item.index))
            block.setEnv('PDG_INDEX4', "{:04d}".format(work_item.index))
            block.setEnv('PDG_SCRIPTDIR', str(script_dir))
            block.setEnv('PDG_JOBID', self.cook_id)
            block.setEnv('PDG_JOBID_VAR', 'PDG_JOBID')

            task = af.Task(task_name)
            task.setCommand(cmd_argv)
            block.tasks.append(task)

            job.blocks.append(block)

            newjid = 0
            logger.debug('onScheduler new job [jid=%d]:' % newjid)

            try:
                newjid = job.send()
                newjid = newjid[1]['id']
            except Exception,err:
                import traceback
                traceback.print_exc()
                sys.stderr.flush()
                raise RuntimeError('Error creating job for ' + item_name + ':\n' + str(err))
            
            # add to active jobs list
            self.active_jobs[newjid] = item_name
            work_item.data.setInt("afanasy_jobid", newjid, 0)

            return pdg.scheduleResult.Succeeded

        except:
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            return pdg.scheduleResult.Failed

    def onScheduleStatic(self, dependencies, dependents, ready_items):
        return

    def onStart(self):
        logger.debug("onStart")
        self.startCallbackServer()
        """
        onStart(self) -> boolean

        [virtual] Scheduler start callback. Starts the XMLRPC server for
        communicating with Tractor.
        """
        return True

    def onStop(self):
        logger.debug("onStop")
        """
        onStop(self) -> boolean

        [virtual] Scheduler stop callback. Shuts down the XMLRPC server for
        communicating with Tractor.
        """
        self.stopCallbackServer()
        self._stopSharedServers()
        return True

    def onStartCook(self, static, cook_set):
        """
        onStartCook(self, static, cook_set) -> boolean

        [virtual] Cook start callback. Starts a root job for the cook session
        """
        self.cook_id = str(int(self.cook_id) + 1)

        # sanity check the local shared root
        localsharedroot = self._localsharedroot()

        # update our working dir
        self._updateWorkingDir()

        file_root = self.workingDir(True)
        if not os.path.exists(file_root):
            os.makedirs(file_root)
        if not os.path.exists(self.tempDir(True)):
            os.makedirs(self.tempDir(True))

        # override the listening port
        overrideportrange = self['overrideportrange'].evaluateInt()
        if overrideportrange > 0:
            callbackportrange = self["callbackportrange"].evaluateInt()
            if callbackportrange != self.custom_port_range:
                self.custom_port_range = callbackportrange
                self.stopCallbackServer()
                self.startCallbackServer()
        
        if not self.isCallbackServerRunning():
            self.startCallbackServer()
        
        self.tick_timer = TickTimer(0.25, self.tick)
        self.tick_timer.start()

        return True

    def tick(self):
        """
        Called during a cook. Checks on jobs in flight to see if
        any have finished.
        """
        # create job command
        cmd = af.Cmd()
        try:
            # check job/task statuses and remove them if finished
            for id in self.active_jobs.keys():
                # query job progress
                if cmd.getJobProgress(id) is None:
                	work_item_name = self.active_jobs[id]
                	self.workItemFailed(work_item_name, -1)
                	del self.active_jobs[id]
                	continue

                query = cmd.getJobProgress(id)['progress'][0]
                job_info = cmd.getJobInfo(id)[0]
                while query:
                    if isinstance(query, dict):
                        break
                    query = query[0]

                # task state 
                task = query['state']

                logger.debug('task_state for {} = {}'.format(id, task))
                if len(task) < 1:
                    # this is a ghost-job - consider it failed
                    work_item_name = self.active_jobs[id]
                    self.workItemFailed(work_item_name, -1)
                    del self.active_jobs[id]
                    continue

                task_state = task.strip()

                if task_state == 'RUN':
                	work_item_name = self.active_jobs[id]
                	self.workItemStartCook(work_item_name, -1)

                if task_state == 'RDY RER':
                    work_item_name = self.active_jobs[id]
                    self.workItemFailed(work_item_name, -1)
                    del self.active_jobs[id]
                    continue

                elif task_state == 'DON' or task_state == 'SKP':
                    statetime = job_info.get('time_started')
                    activetime = job_info.get('time_done')
                    cook_timedelta = float(activetime - statetime)
                    work_item_name = self.active_jobs[id]
                    self.workItemSucceeded(work_item_name, -1, cook_timedelta)
                    del self.active_jobs[id]
                    continue
        except:
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            return False
        return True

    def onStopCook(self, cancel):
        """
        Callback invoked by PDG when graph cook ends.
        """
        if self.tick_timer:
            self.tick_timer.cancel()
        self._stopSharedServers()

        return True

    def submitAsJob(self, graph_file, node_path):
        # we don't support cooking network
        logger.debug("submitAsJob({},{})".format(graph_file, node_path))
        return ""

    def workItemSucceeded(self, name, index, cook_duration, jobid=''):
        """
        Called by CallbackServerMixin when a workitem signals success.
        """
        logger.debug('Job Succeeded: {}'.format(name))
        self.onWorkItemSucceeded(name, index, cook_duration)

    def workItemFailed(self, name, index, jobid=''):
        """
        Called by CallbackServerMixin when a workitem signals failure.
        """
        logger.debug('Job Failed: name={}, index={}, jobid={}'.format(name, index, jobid))
        self.onWorkItemFailed(name, index)

    def workItemCancelled(self, name, index, jobid=''):
        """
        Called by CallbackServerMixin when a workitem signals cancelled.
        """
        logger.debug('Job Cancelled: {}'.format(name))
        self.onWorkItemCanceled(name, index)

    def workItemStartCook(self, name, index, jobid=''):
        """
         Called by CallbackServerMixin when a workitem signals started.
        """
        logger.debug('Job Start Cook: {}'.format(name))
        self.onWorkItemStartCook(name, index)

    def workItemFileResult(self, item_name, subindex, result, tag, checksum, jobid=''):
        """
        Called by CallbackServerMixin when a workitem signals file result data reported.
        """
        self.onWorkItemFileResult(item_name, subindex, result, tag, checksum)

    def workItemSetAttribute(self, item_name, subindex, attr_name, data, jobid=''):
        """
        Called by CallbackServerMixin when a workitem signals simple result data reported.
        """
        self.onWorkItemSetAttribute(item_name, subindex, attr_name, data)


    def _stopSharedServers(self):
    	for sharedserver_name in self.getSharedServers():
    		self.endSharedServer(sharedserver_name)

    def onSharedServerStarted(self, args):
        """
        Called when a job has started a new sharedserver
        """
        logger.debug("sharedserver started: {}, args = {}".format(args["name"], args))
        self.setSharedServerInfo(args["name"], args)
        return True

    def endSharedServer(self, sharedserver_name):
        """
        Called by a job or on cook end to terminate the sharedserver
        """
        try:
            info = self.getSharedServerInfo(sharedserver_name)
            logger.debug("Killing sharedserver: " + sharedserver_name)
            from pdgjob.sharedserver import shutdownServer
            # FIXME:
            # at this point we need to kill the server which is running somewhere on the farm
            # it would be nice to do this directly with hqueue, but the server is not officially a job.
            # This will need to be reworked so that the onFailed/onSuccess callbacks of the top-level
            # job are responsible for cleaning up the server.
            shutdownServer(info)
            # Setting info to empty string removes from the scheduler internal list
            self.clearSharedServerInfo(sharedserver_name)
        except:
            return False
        return True

    def getLogURI(self, work_item):
        log_path = '{}/logs/{}.log'.format(self.tempDir(True), work_item.name)
        uri = 'file:///' + log_path
        return uri

    def getStatusURI(self, work_item):
    	# no seperate status page for afanasy scheduler
        return ''


def registerTypes(type_registry):
    type_registry.registerScheduler(AfanasyScheduler, label="Afanasy Scheduler")