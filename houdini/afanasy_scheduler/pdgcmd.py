#
# PROPRIETARY INFORMATION.  This software is proprietary to
# Side Effects Software Inc., and is not to be reproduced,
# transmitted, or disclosed in any way without written permission.
#
# Produced by:
#	Side Effects Software Inc
#	123 Front Street West, Suite 1401
#	Toronto, Ontario
#	Canada   M5J 2M2
#	416-504-9876
#
# NAME:	        pdgcmd.py ( Python )
#
# COMMENTS:     Utility methods for jobs that need to report back to PDG.
#               Not dependent on Houdini install.
#

import json
import logging
import os
import socket
import subprocess
import sys
import time
import xmlrpclib
import httplib
import shlex

logging.basicConfig(level=logging.DEBUG)

#
# Path Utilities

def delocalizePath(local_path):
    """
    Delocalize the given path to be rooted at __PDG_DIR__
    Requires PDG_DIR env var to be present
    """
    # de-localize the result_data path if possible
    # we do this by replacing the file prefix if it matches our expected env var
    deloc_path = local_path
    try:
        pdg_dir_local = os.environ['PDG_DIR']
        # our env var value might be in terms of another env var - so expand again
        pdg_dir_local = os.path.expandvars(pdg_dir_local)
        # normalize path to forward slashes
        pdg_dir_local = pdg_dir_local.replace('\\','/')
        deloc_path = local_path.replace('\\','/')
        deloc_path = deloc_path.replace(pdg_dir_local, '__PDG_DIR__', 1)
    except KeyError:
        pass
    return deloc_path

# Makes a directory if it does not exist, and is made to be safe against 
# directory creation happening concurrent while we're attemtping to make it
def makeDirSafe(local_path):
    if not local_path:
        return

    try:
        os.makedirs(local_path)
    except OSError:
        if not os.path.isdir(local_path):
            raise

def _substitute_scheduler_vars(data):
    for var in ('PDG_DIR', 'PDG_ITEM_NAME', 'PDG_TEMP', 'PDG_RESULT_SERVER', 'PDG_INDEX'):
        varsym = '__' + var + '__'
        if varsym in data:
            data = data.replace(varsym, os.environ[var])
    return data

def localizePath(deloc_path):
    """
    Localize the given path.  This means replace any __PDG* tokens and
    expand env vars with the values in the current environment
    """
    loc_path = _substitute_scheduler_vars(deloc_path)
    loc_path = os.path.expandvars(loc_path)
    # support env vars defined as other env vars
    loc_path = os.path.expandvars(loc_path)
    loc_path = loc_path.replace("\\", "/")
    return loc_path

# Callback Helper Functions.
# These functions are used in task code to report status and results
# to the PDG callback server
#

def execBatchPoll(item_name, subindex, server_addr):
    """
    Blocks until a batch sub item can begin cooking
    """
    s = xmlrpclib.ServerProxy('http://'+server_addr)
    while True:
        r = s.check_ready_batch(item_name, subindex)
        if r and int(r)==1:
            break
        time.sleep(0.5)

def execItemFailed(item_name, server_addr, to_stdout = True):
    """
    Executes an item callback directly to report when an item has failed.

    item_name: name of the associated workitem
    server_addr: callback server in format 'IP:PORT', or emptry string to ignore
    to_stdout: also emit status messages to stdout

    If there is an error connecting to the callback server an error will be printed, but no 
    exception raised.

    Note: Batch items not supported.
    """
    try:
        jobid = os.environ[os.environ['PDG_JOBID_VAR']]
    except:
        jobid = ''

    s = xmlrpclib.ServerProxy('http://' + server_addr)
    s.failed(item_name, jobid)

def execStartCook(item_name, subindex=-1, server_addr="", to_stdout = True):
    """
    Executes an item callback directly to report than a work item with a
    specific index has started cooking
    """
    if to_stdout:
        print("PDG_START: {};{}".format(item_name, subindex))

    try:
        jobid = os.environ[os.environ['PDG_JOBID_VAR']]
    except:
        jobid = ''
    
    s = xmlrpclib.ServerProxy('http://' + server_addr)
    if subindex >= 0:
        s.start_cook_batch(item_name, subindex, jobid)
    else:
        s.start_cook(item_name, jobid)

def reportResultData(result_data, item_name=None, server_addr=None,
                     result_data_tag="", subindex=-1, and_success=False, to_stdout = True,
                     duration = 0.0, hash_code = 0):
    """
    Reports a result to PDG via the callback server.

    item_name:      name of the associated workitem (default $PDG_ITEM_NAME)
    server_addr:    callback server in format 'IP:PORT' (default $PDG_RESULT_SERVER)
                    if there is no env var it will default to stdout reporting only.
    result_data:    result data - treated as bytes if result_data_tag is passed
    result_data_tag: result tag to categorize result.  Eg: 'file/geo'
                     Default is empty which means attempt to categorize using file extension.
    subindex:       The batch subindex if this is a batch item.
    and_success:    If True, report success in addition to result_data
    to_stdout:      also emit status messages to stdout
    duration:       cook time of the item in seconds, only report with and_success 
    hash_code:      hashcode for result
    """
    if not isinstance(result_data, (list, tuple)):
        result_data_list = [result_data]
    else:
        result_data_list = result_data

    if not result_data_list:
        raise TypeError("result_data is invalid")
    if not isinstance(result_data_list[0], (bytes,bytearray,unicode)):
        raise TypeError("result_data must be string-like or a list of string-like")

    if not item_name:
        item_name = os.environ['PDG_ITEM_NAME']
    
    do_socket = True
    if not server_addr:
        try:
            server_addr = os.environ['PDG_RESULT_SERVER']
        except KeyError:
            do_socket = False

    is_filepath = result_data_tag.startswith('file') or not result_data_tag
    
    server_proxy = xmlrpclib.ServerProxy('http://' + server_addr)

    multicall = False
    proxy = server_proxy
    if len(result_data_list) > 1:
        proxy = xmlrpclib.MultiCall(server_proxy)
        multicall = True

    try:
        jobid = os.environ[os.environ['PDG_JOBID_VAR']]
    except:
        jobid = ''

    for result_data_elem in result_data_list:
        if is_filepath:
            # de-localize the result_data path if possible
            # we do this by replacing the file prefix if it matches our expected env var
            if not result_data_elem.startswith('__PDG_DIR__'): 
                result_data_elem = delocalizePath(result_data_elem)

        log_dir = os.environ['PDG_SHARED_TEMP']
        item_log_path = os.path.join(log_dir, 'logs', item_name).replace('\\', '/') + '.log'

        def open_output_file():
            outf = open(item_log_path, 'w')
            return outf

        output_file = open_output_file()
        if to_stdout:
            if len(result_data_elem) > 100:
                print_result_data_elem = repr(result_data_elem)[0:90] + '...('+str(len(result_data_elem))+' bytes)'
            else:
                print_result_data_elem = repr(result_data_elem)
            print("PDG_RESULT: {};{};{};{};{}".format(item_name, subindex, print_result_data_elem, result_data_tag, hash_code))
            output_file.write("PDG_RESULT: {};{};{};{};{}".format(item_name, subindex, print_result_data_elem, result_data_tag, hash_code))
            if and_success:
                print("PDG_SUCCESS: {};{};{}".format(item_name, subindex, duration))
            output_file.close()

        '''
        if do_socket:
            if and_success:
                if subindex >= 0:
                    proxy.success_and_result_batch(item_name, xmlrpclib.Binary(result_data_elem),
                        result_data_tag, subindex, hash_code, duration, jobid)
                else:
                    proxy.success_and_result(item_name, xmlrpclib.Binary(result_data_elem),
                        result_data_tag, hash_code, duration, jobid)
            else:
                if subindex >= 0:
                    proxy.result_batch(item_name, xmlrpclib.Binary(result_data_elem), 
                        result_data_tag, subindex, hash_code, jobid)
                else:
                    proxy.result(item_name, xmlrpclib.Binary(result_data_elem), 
                        result_data_tag, hash_code, jobid)
        '''
    
    if multicall:
        proxy()

    return True

def writeAttribute(attr_name, attr_value, item_name=None, server_addr=None):
    """
    Writes attribute data back into a work item in PDG via the callback server.

    item_name:      name of the associated workitem (default $PDG_ITEM_NAME)
    server_addr:    callback server in format 'IP:PORT' (default $PDG_RESULT_SERVER)
                    if there is no env var it will default to stdout reporting only.
    attr_name:      name of the attribute
    attr_value:     single value or array of string/float/int data
    """
    if not isinstance(attr_value, (list, tuple)):
        attr_value_list = [attr_value]
    else:
        attr_value_list = attr_value

    if not attr_value_list:
        raise TypeError("attr_value is invalid")
    if not isinstance(attr_value_list[0], (bytes,bytearray,unicode,int,float)):
        raise TypeError("result_data must be string, int or float (array)")

    if not item_name:
        item_name = os.environ['PDG_ITEM_NAME']
    
    if not server_addr:
        server_addr = os.environ['PDG_RESULT_SERVER']

    server_proxy = xmlrpclib.ServerProxy('http://' + server_addr)

    proxy = server_proxy
    try:
        jobid = os.environ[os.environ['PDG_JOBID_VAR']]
    except:
        jobid = ''

    print("PDG_RESULT_ATTR: {};{};{}".format(item_name, attr_name, attr_value_list))
    proxy.write_attr(item_name, attr_name, attr_value_list, jobid)

def reportServerStarted(servername, pid, host, port, proto_type, item_name=None, server_addr=None):
    """
    Reports that a shared server has been started.

    item_name:      name of the associated workitem (default $PDG_ITEM_NAME)
    server_addr:    callback server in format 'IP:PORT' (default $PDG_RESULT_SERVER)
    """
    sharedserver_message = {
        "name" : servername,
        "pid" : pid, 
        "host" : host,
        "port" : port,
        "proto_type" : proto_type
        }

    if not item_name:
        item_name = os.environ['PDG_ITEM_NAME']
    
    if not server_addr:
        server_addr = os.environ['PDG_RESULT_SERVER']

    server_proxy = xmlrpclib.ServerProxy('http://' + server_addr)
    try:
        jobid = os.environ[os.environ['PDG_JOBID_VAR']]
    except:
        jobid = ''

    server_proxy.sharedserver_started(sharedserver_message, jobid)
    reportResultData(str(host), item_name=item_name,
        server_addr=server_addr, result_data_tag="socket/ip")
    reportResultData(str(port), item_name=item_name,
        server_addr=server_addr, result_data_tag="socket/port")

def getSharedServerInfo(servername, server_addr=None):
    """
    Returns the dict of server info 
    """
    if not server_addr:
        server_addr = os.environ['PDG_RESULT_SERVER']

    server_proxy = xmlrpclib.ServerProxy('http://' + server_addr)
    return server_proxy.get_sharedserver_info(servername)

def execCommand(command, toolName=None):
    """
    Executes a command
    """

    print "Executing command: {}".format(command)

    try:
        process = subprocess.Popen(shlex.split(command))
        process.communicate()
        if process.returncode != 0:
            exit(1)
    except subprocess.CalledProcessError as cmd_err:
        print "ERROR: problem executing command {}".format(command)
        print cmd_err
        exit(1)
    except OSError as os_err:

        # OSError might be due to missing executable, if that's the
        # case, inform the user about it.
        # We could check this before trying to execute, but considering this is
        # the exception, I'd rather not check this every time we run the command

        try:
            import distutils.spawn

            executableName = command.split(' ')[0]
            if not distutils.spawn.find_executable(executableName):
                print "ERROR: could not find executable {}".format(executableName)
                print "Are you sure you have {} installed?".format(toolName or executableName)
            else:
                print "ERROR: problem executing command {}".format(command)
                print os_err
        except:
            print "ERROR: problem executing command {}".format(command)
            print os_err


        exit(1)
