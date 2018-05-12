import json
from debug_adapter import schema, base_schema
from debug_adapter.log import DEBUG, debug, debug_exception
import itertools
import threading



def read(stream):
    '''
    Reads one message from the stream and returns the related dict (or None if EOF was reached).
    
    :param stream:
        The stream we should be reading from.
        
    :return dict|NoneType:
        The dict which represents a message or None if the stream was closed.
    '''
    headers = {}
    while True:
        # Interpret the http protocol headers
        line = stream.readline() # The trailing \r\n should be there.
        
        if DEBUG:
            debug('read line: >>%s<<\n' % (line.replace('\r', '\\r').replace('\n', '\\n')),)
            
        if not line:  # EOF
            return None
        line = line.strip().decode('ascii')
        if not line:  # Read just a new line without any contents
            break
        try:
            name, value = line.split(': ', 1)
        except ValueError:
            raise RuntimeError('invalid header line: {}'.format(line))
        headers[name] = value

    if not headers:
        raise RuntimeError('got message without headers')

    size = int(headers['Content-Length'])
    
    # Get the actual json
    body = stream.read(size)

    return json.loads(body.decode('utf-8'))


def reader_thread(stream, process_command):
    try:
        while True:
            data = read(stream)
            if data is None:
                break
            protocol_message = base_schema.from_dict(data)
            process_command(protocol_message)
    except:
        debug_exception()


try:
    _next_seq = itertools.count().__next__
except:
    _next_seq = itertools.count().next

    
def writer_thread(stream, queue):
    try:
        while True:
            to_write = queue.get()
            to_json = getattr(to_write, 'to_json', None)
            if to_json is not None:
                # Some protocol message
                to_write.seq = _next_seq()
                try:
                    to_write = to_json()
                except:
                    debug_exception('Error serializing %s to json.' % (to_write,))
                    continue
                
            if DEBUG:
                debug('Writing: %s\n' % (to_write,))
                
            if to_write.__class__ == bytes:
                as_bytes = to_write
            else:
                as_bytes = to_write.encode('utf-8')
                
            stream.write('Content-Length: %s\r\n\r\n' % (len(as_bytes)))
            stream.write(as_bytes)
            stream.flush()
    except:
        debug_exception()


class CommandProcessor(object):
    '''
    This is the class that actually processes commands.
    
    It's created in the main thread and then control is passed on to the reader thread so that whenever
    something is read the json is handled by this processor.
    
    The queue it receives in the constructor should be used to talk to the writer thread, where it's expected 
    to post protocol messages (which will be converted with 'to_dict()' and will have the 'seq' updated as 
    needed).
    '''

    def __init__(self, write_queue):
        self.write_queue = write_queue
        self._launch_process = None #: :type self._launch_process: LaunchProcess
        self._supports_run_in_terminal = False
        
    @property
    def supports_run_in_terminal(self):
        return self._supports_run_in_terminal

    def __call__(self, protocol_message):
        if DEBUG:
            debug('Process json: %s\n' % (
                json.dumps(protocol_message.to_dict(), indent=4, encoding='utf-8', sort_keys=True),))
        
        try:
            if protocol_message.type == 'request':
                method_name = 'on_%s_request' % (protocol_message.command,)
                on_request = getattr(self, method_name, None)
                if on_request is not None:
                    on_request(protocol_message)
                else:
                    if DEBUG:
                        debug('Unhandled: %s not available in CommandProcessor.\n' % (method_name,))
        except:
            debug_exception()
    
    def on_initialize_request(self, request):
        '''
        :param InitializeRequest request:
        '''
        # : :type initialize_response: InitializeResponse
        # : :type body: Capabilities
        initialize_response = base_schema.build_response(request)
        self._supports_run_in_terminal = request.arguments.supportsRunInTerminalRequest
        body = initialize_response.body
        body.supportsConfigurationDoneRequest = True
        body.supportsConditionalBreakpoints = True
        self.write_message(initialize_response)
        self.write_message(schema.InitializedEvent())
        
    def on_launch_request(self, request):
        '''
        :param LaunchRequest request:
        '''
        from debug_adapter.launch_process import LaunchProcess
        # : :type launch_response: LaunchResponse
        launch_response = base_schema.build_response(request)
        
        self._launch_process = launch_process = LaunchProcess(request, launch_response, self)
        if launch_process.valid:
            launch_process.launch()
            
        self.write_message(launch_response)  # acknowledge it
        
        
    def on_configurationDone_request(self, request):
        '''
        :param ConfigurationDoneRequest request:
        '''
        # : :type configuration_done_response: ConfigurationDoneResponse
        configuration_done_response = base_schema.build_response(request)
        self.write_message(configuration_done_response)  # acknowledge it
        
    def on_threads_request(self, request):
        '''
        :param ThreadsRequest request:
        '''
        from debug_adapter.schema import ThreadsResponseBody, Thread
        
        # TODO: Get the actual threads
        threads = [
            Thread(0, 'Main Thread').to_dict(),
            Thread(1, 'Thread 1').to_dict(),
        ]
        kwargs = {'body':ThreadsResponseBody(threads)}
        # : :type threads_response: ThreadsResponse
        threads_response = base_schema.build_response(request, kwargs)
        self.write_message(threads_response)
        
    def on_disconnect_request(self, request):
        '''
        :param DisconnectRequest request:
        '''
        # TODO: Actually terminate our process (see request.arguments.terminateDebuggee for customization).
        
        # : :type disconnect_response: DisconnectResponse
        disconnect_response = base_schema.build_response(request)
        
        if self._launch_process is not None:
            self._launch_process.disconnect(request)
        
        self.write_message(disconnect_response)
        
    def on_pause_request(self, request):
        '''
        :param PauseRequest request:
        '''
        # : :type pause_response: PauseResponse
        pause_response = base_schema.build_response(request)
        self.write_message(pause_response)
        
        # TODO: Actually ask backend to pause threads!
        
    def on_evaluate_request(self, request):
        '''
        :param EvaluateRequest request:
        '''
        if self._launch_process is not None:
            if request.arguments.context == 'repl':
                self._launch_process.send_to_stdin(request.arguments.expression)
                
        evaluate_response = base_schema.build_response(request, kwargs={
            'body':{'result': '', 'variablesReference': 0}})
        self.write_message(evaluate_response)
        
        
    def write_message(self, protocol_message):
        '''
        :param BaseSchema protocol_message: 
            Some instance of one of the messages in the debug_adapter.schema.
        '''
        self.write_queue.put(protocol_message)

    
def main():
    '''
    Starts the debug adapter (creates a thread to read from stdin and another to write to stdout as 
    expected by the vscode debug protocol).
    
    We pass the command processor to the reader thread as the idea is that the reader thread will
    read a message, convert it to an instance of the message in the schema and then forward it to
    the command processor which will interpret and act on it, posting the results to the writer queue.
    '''
    try:
        import sys
        try:
            from queue import Queue
        except ImportError:
            from Queue import Queue

        write_queue = Queue()
        command_processor = CommandProcessor(write_queue)

        if DEBUG:
            debug('Starting. Args: %s\n' % (', '.join(sys.argv),))

        write_to = sys.stdout
        read_from = sys.stdin
        
        if sys.version_info[0] <= 2:
            if sys.platform == "win32":
                # must read streams as binary on windows
                import os, msvcrt
                msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
                msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        else:
            # Py3
            write_to = sys.stdout.buffer
            read_from = sys.stdin.buffer

        writer = threading.Thread(target=writer_thread, args=(write_to, write_queue))
        reader = threading.Thread(target=reader_thread, args=(read_from, command_processor))

        reader.start()
        writer.start()

        reader.join()
        writer.join()
    except:
        debug_exception()

    debug('exiting main.\n')


if __name__ == '__main__':
    main()
