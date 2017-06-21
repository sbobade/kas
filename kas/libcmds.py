# kas - setup tool for bitbake based projects
#
# Copyright (c) Siemens AG, 2017
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
    This module contain common commands used by kas plugins.
"""

import tempfile
import logging
import shutil
import os
from .libkas import (ssh_cleanup_agent, ssh_setup_agent, ssh_no_host_key_check,
                     run_cmd, get_oe_environ)

__license__ = 'MIT'
__copyright__ = 'Copyright (c) Siemens AG, 2017'


class Macro:
    """
        Contains commands and provide method to run them.
    """
    def __init__(self):
        self.commands = []

    def add(self, command):
        """
            Appends commands to the command list.
        """
        self.commands.append(command)

    def run(self, config, skip=None):
        """
            Runs command from the command list respective to the configuration.
        """
        skip = skip or []
        for command in self.commands:
            command_name = str(command)
            if command_name in skip:
                continue
            pre_hook = config.pre_hook(command_name)
            if pre_hook:
                logging.debug('execute %s', pre_hook)
                pre_hook(config)
            command_hook = config.get_hook(command_name)
            if command_hook:
                logging.debug('execute %s', command_hook)
                command_hook(config)
            else:
                logging.debug('execute %s', command_name)
                command.execute(config)
            post_hook = config.post_hook(command_name)
            if post_hook:
                logging.debug('execute %s', post_hook)
                post_hook(config)


class Command:
    """
        An abstract class that defines the interface of a command.
    """

    def execute(self, config):
        """
            This method executes the command.
        """
        pass


class SetupHome(Command):
    """
        Setups the home directory of kas.
    """

    def __init__(self):
        super().__init__()
        self.tmpdirname = tempfile.mkdtemp()

    def __del__(self):
        shutil.rmtree(self.tmpdirname)

    def __str__(self):
        return 'setup_home'

    def execute(self, config):
        with open(self.tmpdirname + '/.wgetrc', 'w') as fds:
            fds.write('\n')
        with open(self.tmpdirname + '/.netrc', 'w') as fds:
            fds.write('\n')
        config.environ['HOME'] = self.tmpdirname


class SetupDir(Command):
    """
        Creates the build directory.
    """

    def __str__(self):
        return 'setup_dir'

    def execute(self, config):
        os.chdir(config.kas_work_dir)
        if not os.path.exists(config.build_dir):
            os.mkdir(config.build_dir)


class SetupSSHAgent(Command):
    """
        Setup the ssh agent configuration.
    """

    def __str__(self):
        return 'setup_ssh_agent'

    def execute(self, config):
        ssh_setup_agent(config)
        ssh_no_host_key_check(config)


class CleanupSSHAgent(Command):
    """
        Remove all the identities and stop the ssh-agent instance.
    """

    def __str__(self):
        return 'cleanup_ssh_agent'

    def execute(self, config):
        ssh_cleanup_agent(config)


class SetupProxy(Command):
    """
        Setups proxy configuration in the kas environment.
    """

    def __str__(self):
        return 'setup_proxy'

    def execute(self, config):
        config.environ.update(config.get_proxy_config())


class SetupEnviron(Command):
    """
        Setups the kas environment.
    """

    def __str__(self):
        return 'setup_environ'

    def execute(self, config):
        config.environ.update(get_oe_environ(config, config.build_dir))


class WriteConfig(Command):
    """
        Writes bitbake configuration files into the build directory.
    """

    def __str__(self):
        return 'write_config'

    def execute(self, config):
        def _write_bblayers_conf(config):
            filename = config.build_dir + '/conf/bblayers.conf'
            with open(filename, 'w') as fds:
                fds.write(config.get_bblayers_conf_header())
                fds.write('BBLAYERS ?= " \\\n')
                for repo in config.get_repos():
                    fds.write(' \\\n'.join(repo.layers + ['']))
                fds.write('"\n')

        def _write_local_conf(config):
            filename = config.build_dir + '/conf/local.conf'
            with open(filename, 'w') as fds:
                fds.write(config.get_local_conf_header())
                fds.write('MACHINE ?= "{}"\n'.format(config.get_machine()))
                fds.write('DISTRO ?= "{}"\n'.format(config.get_distro()))

        _write_bblayers_conf(config)
        _write_local_conf(config)


class ReposFetch(Command):
    """
        Fetches repositories defined in the configuration
    """

    def __str__(self):
        return 'repos_fetch'

    def execute(self, config):
        for repo in config.get_repos():
            if repo.git_operation_disabled:
                continue

            if not os.path.exists(repo.path):
                os.makedirs(os.path.dirname(repo.path), exist_ok=True)
                gitsrcdir = os.path.join(config.get_repo_ref_dir() or '',
                                         repo.qualified_name)
                logging.debug('Looking for repo ref dir in %s', gitsrcdir)
                if config.get_repo_ref_dir() and os.path.exists(gitsrcdir):
                    run_cmd(['/usr/bin/git',
                             'clone',
                             '--reference', gitsrcdir,
                             repo.url, repo.path],
                            env=config.environ,
                            cwd=config.kas_work_dir)
                else:
                    run_cmd(['/usr/bin/git', 'clone', '-q', repo.url,
                             repo.path],
                            env=config.environ,
                            cwd=config.kas_work_dir)
                continue

            # Does refspec in the current repository?
            (retc, output) = run_cmd(['/usr/bin/git', 'cat-file',
                                      '-t', repo.refspec], env=config.environ,
                                     cwd=repo.path, fail=False)
            if retc == 0:
                continue

            # No it is missing, try to fetch
            (retc, output) = run_cmd(['/usr/bin/git', 'fetch', '--all'],
                                     env=config.environ,
                                     cwd=repo.path, fail=False)
            if retc:
                logging.warning('Could not update repository %s: %s',
                                repo.name, output)


class ReposCheckout(Command):
    """
        Ensures that the right revision of each repo is check out.
    """

    def __str__(self):
        return 'repos_checkout'

    def execute(self, config):
        for repo in config.get_repos():
            if repo.git_operation_disabled:
                continue

            # Check if repos is dirty
            (_, output) = run_cmd(['/usr/bin/git', 'diff', '--shortstat'],
                                  env=config.environ, cwd=repo.path,
                                  fail=False)
            if len(output):
                logging.warning('Repo %s is dirty. no checkout', repo.name)
                continue

            # Check if current HEAD is what in the config file is defined.
            (_, output) = run_cmd(['/usr/bin/git', 'rev-parse',
                                   '--verify', 'HEAD'],
                                  env=config.environ, cwd=repo.path)

            if output.strip() == repo.refspec:
                logging.info('Repo %s has already checkout out correct '
                             'refspec. nothing to do', repo.name)
                continue

            run_cmd(['/usr/bin/git', 'checkout', '-q',
                     '{refspec}'.format(refspec=repo.refspec)],
                    cwd=repo.path)
