# Copyright (c) Ran Dugal 2014
#
# This file is part of dust.
#
# Licensed under the GNU Affero General Public License v3, which is available at
# http://www.gnu.org/licenses/agpl-3.0.html
# 
# This program is distributed in the hope that it will be useful, but WITHOUT 
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero GPL for more details.
#

'''
Dust command loop
'''

import sys
import socket
import os
import readline
import logging
import stat
import colorama

from collections import defaultdict
from cmd import Cmd
from EC2 import EC2Config

import paramiko
from dustcluster import commands, lineterm
from dustcluster import __version__
from dustcluster.cluster import ClusterCommandEngine
from dustcluster.config import DustConfig

import atexit

from dustcluster import util
logger = util.setup_logger( __name__ )

if os.environ.get('COLORTERM'):
    colorama.Fore.CYAN  = '\x1b[38;5;75m'
    colorama.Fore.GREEN = '\x1b[38;5;76m'

class Console(Cmd):
    ''' command line tool to control a cloud cluster '''

    dustintro  = "Dust cluster shell, version %s. Type ? for help." % __version__

    def __init__(self):

        util.intro()

        logger.setLevel(logging.INFO)

        # read/create config
        try:
            self.config = DustConfig()
        except Exception, e:
            logger.error("Error getting config/credentials. Cannot continue.")
            raise

        # load history
        try:
            if os.path.exists(self.config.get_history_file_path()):
                readline.read_history_file(self.config.get_history_file_path())

        except IOError:
            logger.warning("Error reading history file. No command history available.")

        atexit.register(self.on_exit, None)

        Cmd.__init__(self)

        self.commands = {}  # { cmd : (helpstr, module) }
        # startup
        self.cluster = ClusterCommandEngine()
        self.cluster.load_commands()
 
        self.exit_flag = False
        self.cluster.lineterm.set_refresh_callback(self.redisplay)

        self.cluster.handle_command('loglevel',  self.config.get_userdata().get('loglevel') or 'info')
        logger.info(self.dustintro)
    
    @property
    def prompt(self):
        if self.cluster.cloud and self.cluster.cloud.region:
            return "[%s]$ " % self.cluster.cloud.region
        else:
            return "[dust]$ "


    def redisplay(self):
        ''' refresh the prompt '''
        sys.stdout.write('\n\r' + self.prompt)
        sys.stdout.write(readline.get_line_buffer())
        sys.stdout.flush()

    def on_exit(self, _ ):
        readline.write_history_file(self.config.get_history_file_path())

        confregion = self.config.user_data['region']
        cloud_region = ""
        if self.cluster and self.cluster.cloud:
            cloud_region = self.cluster.cloud.region

        if confregion != cloud_region:
            self.config.user_data['region'] = cloud_region 
            self.config.write_user_data()

    def emptyline(self):
        pass

    def do_help(self, args):
        '''
        help [cmd] - Show help on command cmd. 
        Modified from base class.
        '''

        commands = self.cluster.get_commands()

        if args:
            if args in commands:
                docstr, _ = commands.get(args)
                print colorama.Fore.GREEN, docstr, colorama.Style.RESET_ALL
                return
            return Cmd.do_help(self, args)

        print self.dustintro
        print "\nAvailable commands:\n"

        # generate help summary from docstrings

        names = dir(self.__class__)
        prevname = ""
        for name in names:
            if name[:3] == 'do_':
                if name == prevname:
                    continue
                cmd = name[3:]
                docstr = ""
                if getattr(self, name):
                    docstr = getattr(self, name).__doc__
                self._print_cmd_help(docstr, cmd)

        # show help from drop-in commands
        modcommands = defaultdict(list)
        for cmd, (docstr, mod) in commands.items():
            modcommands[mod].append( (cmd, docstr) )

        for mod, cmds in modcommands.iteritems():
            print "\n== From %s:" % mod.__name__
            for (cmd, docstr) in cmds: 
                self._print_cmd_help(docstr, cmd)

        print '\nType help [command] for detailed help on a command'

        print '\nFor most commands, [target] can be a node name, regular expression, or filter expression'
        print 'A node "name" in these commands is the Name tag in the cloud node metadata or in the cluster definition.'
        print '\n'


    def _print_cmd_help(self, docstr, cmd):
        ''' format and print the cmd help string '''
        if docstr and '\n' in docstr:
            helpstr = docstr.split('\n')[1]
            if '-' in helpstr:
                pos = helpstr.rfind('-')
                cmd, doc = helpstr[:pos].strip(), helpstr[pos+1:].strip()
            else:
                doc = ""
            print "%-40s%s%s%s" % (cmd.strip(), colorama.Fore.GREEN, doc.strip(), colorama.Style.RESET_ALL)
        else:
            print "%-40s" % cmd.strip()


    def default(self, line):

        # handle a cluster command
        cmd, arg, line = self.parseline(line)
        if not self.cluster.handle_command(cmd, arg):
            # not handled? try system shell
            logger.info( 'dustcluster: [%s] unrecognized, trying system shell...\n' % line )
            os.system(line)

        return

    def parseline(self, line):

        # expand @target cmd to atssh target cmd
        if line and line[0] == '@':
            tokens = line.split()
            if len(tokens[0]) == 1:
                line = 'atssh * ' + line[1:]
            else:
                target  = tokens[0][1:]
                line = 'atssh %s %s ' % (target, line[len(tokens[0]):] )

        ret = Cmd.parseline(self, line)
        return ret


    def do_exit(self, _):
        '''
        exit - exit dust shell
        '''     
        logger.info( 'Exiting dust console. Find updates, file bugs at http://github.com/carlsborg/dust.')
        logger.info( '%sThis is an early beta release. Consider updating with $pip install dustcluster --upgrade"%s.' %
                     (colorama.Fore.GREEN, colorama.Style.RESET_ALL))

        self.cluster.logout()
        self.exit_flag = True
        return True

    def do_EOF(self, line):
        '''
        EOF/Ctrl D - exit dust shell
        '''
        return self.do_exit(line)

