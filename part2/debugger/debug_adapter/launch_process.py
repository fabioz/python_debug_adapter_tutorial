from debug_adapter.log import DEBUG, debug, debug_exception


def _read_stream(stream, on_line, category):
    try:
        while True:
            output = stream.readline()
            if len(output) == 0:
                break
            on_line(output, category)
    except:
        debug_exception()

        
def _notify_on_exited(process, on_exited):
    try:
        process.wait()
        if DEBUG:
            debug('notify process exited\n')
        on_exited()
    except:
        debug_exception()

        
class LaunchProcess(object):
    
    __slots__ = [
        '_valid',
        '_cmdline',
        '_console',
        '_popen',
        '_weak_command_processor',
        '__weakref__',
        '_cwd',
        '_run_in_debug_mode'
    ]
    
    def __init__(self, request, launch_response, command_processor):
        '''
        :param LaunchRequest request:
        :param LaunchResponse launch_response:
        '''
        from debug_adapter._constants import VALID_CONSOLE_OPTIONS
        import weakref
        
        self._weak_command_processor = weakref.ref(command_processor)
        self._valid = True
        self._cmdline = []
        self._popen = None
        
        import sys
        import os.path
        
        file_to_run = request.arguments.kwargs.get('program')
        self._cwd = request.arguments.kwargs.get('cwd')
        self._console = request.arguments.kwargs.get('console')
        self._run_in_debug_mode = not request.arguments.noDebug
        
        if self._console not in VALID_CONSOLE_OPTIONS:
            launch_response.success = False
            launch_response.message = 'Invalid console option: %s (must be one of: %s)' % (
                self._console, VALID_CONSOLE_OPTIONS) 
            return
        
        if not os.path.exists(self._cwd):
            launch_response.success = False
            launch_response.message = 'cwd specified does not exist: %s' % (self._cwd,) 
            return
            
        if not os.path.exists(file_to_run):
            launch_response.success = False
            launch_response.message = 'File: %s does not exist.' % (file_to_run,)
            self._valid = False
            return
        
        # TODO: Properly handle debug/no debug mode
        if DEBUG:
            debug('Run in debug mode: %s\n' % (self._run_in_debug_mode,))
            
        cmdline = [sys.executable, '-u', file_to_run]
        self._cmdline = cmdline

    @property
    def valid(self):
        return self._valid
    
    def launch(self):
        from debug_adapter import schema
        from debug_adapter._constants import CONSOLE_EXTERNAL
        from debug_adapter._constants import CONSOLE_INTEGRATED
        from debug_adapter._constants import CONSOLE_NONE
        import threading
        
        # Note: using a weak-reference so that callbacks don't keep it alive            
        weak_command_processor = self._weak_command_processor
        
        console = self._console
        if not weak_command_processor().supports_run_in_terminal:
            # If the client doesn't support running in the terminal we fallback to using the debug console.
            console = CONSOLE_NONE
        
        def on_exited():
            command_processor = weak_command_processor()
            if command_processor is not None:
                restart = False
                terminated_event = schema.TerminatedEvent(body=schema.TerminatedEventBody(restart=restart))
                command_processor.write_message(terminated_event)
                
        threads = []
        if console == CONSOLE_NONE:
            import subprocess
            
            if DEBUG:
                debug('Launching in "none" console: %s' % (self._cmdline,))
                
            self._popen = subprocess.Popen(self._cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, cwd=self._cwd)

            def on_output(output, category):
                command_processor = weak_command_processor()
                if command_processor is not None:
                    output_event = schema.OutputEvent(schema.OutputEventBody(output, category=category))
                    command_processor.write_message(output_event)
            
            threads.append(threading.Thread(target=_read_stream, args=(self._popen.stdout, on_output, 'stdout')))
            threads.append(threading.Thread(target=_read_stream, args=(self._popen.stderr, on_output, 'stderr')))
            threads.append(threading.Thread(target=_notify_on_exited, args=(self._popen, on_exited)))
            
        elif console in (CONSOLE_INTEGRATED, CONSOLE_EXTERNAL):
            kind = 'external'
            if console == CONSOLE_INTEGRATED:
                kind = 'internal'
            
            if DEBUG:
                debug('Launching in "%s" console: %s' % (kind, self._cmdline,))
                
            command_processor = weak_command_processor()
            if command_processor is not None:
                # TODO: Provide an env
                command_processor.write_message(schema.RunInTerminalRequest(schema.RunInTerminalRequestArguments(
                    cwd=self._cwd, args=self._cmdline, kind=kind)))
                
                # When the user runs in the integrated terminal or in the external terminal, in regular run mode (i.e.:
                # no debug) , say that this session has been finished (he can close the process from the related
                # terminal).
                on_exited()
        
        for t in threads:
            t.setDaemon(True)
            
        for t in threads:
            t.start()
        
    def disconnect(self, disconnect_request):
        if self._popen is not None:
            # TODO: Also kill child processes launched.
            self._popen.kill()
        
    def send_to_stdin(self, expression):
        popen = self._popen
        if popen is not None:
            import threading
            try:
                debug('Sending: %s to stdin.' % (expression,))
                def write_to_stdin(popen, expression):
                    popen.stdin.write(expression)
                    if not expression.endswith('\r') and not expression.endswith('\n'):
                        popen.stdin.write('\n')
                    popen.stdin.flush()
                    
                # Do it in a thread (in theory the OS could have that filled up and we would never complete
                # trying to write -- although probably a bit far fetched, let's code as if that could actually happen).
                t = threading.Thread(target=write_to_stdin, args=(popen, expression))
                t.setDaemon(True)
                t.start()
            except:
                debug_exception('Error writing: >>%s<< to stdin.' % (expression,))