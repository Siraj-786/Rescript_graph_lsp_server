import os
import json
import subprocess
import time
import sys
import platform
import re
import networkx as nx
import pickle
import argparse
import concurrent.futures
from urllib.parse import urlparse
from tqdm import tqdm
import queue
import threading
from lsprotocol.types import (
    InitializeParams,
    MessageType,
    Position,
    TextDocumentIdentifier,
    TextDocumentItem,
    DidOpenTextDocumentParams,
    InitializeResult,
    DocumentSymbolParams,
    DocumentSymbol,
    Location,
    ReferenceContext,
    ReferenceParams,
    DefinitionParams,
    TypeDefinitionParams,
    ClientCapabilities,
    WorkspaceClientCapabilities,
    InitializedParams
)
from lsprotocol import converters
from typing import List, Optional, Dict

# --- LSP Converter ---
converter = converters.get_converter()

# --- Global state for LSP communication ---
request_id_counter = 1
lsp_responses = {}
lsp_response_events = {}
message_queue = queue.Queue()
file_locks = {}
file_analysis_cache = {}
graph_lock = threading.Lock()

# --- Configuration ---
# Path to your ReScript project root
parser = argparse.ArgumentParser()
parser.add_argument("project_root", nargs="?", default=".", help="The root directory of the ReScript project to analyze.")
args = parser.parse_args()
PROJECT_ROOT = os.path.abspath(args.project_root)

# Determine the platform-specific binary path for the language server
platform_name = sys.platform
arch = platform.machine()

# Normalize arch names to match Node.js 'process.arch'
if arch == 'x86_64':
    arch = 'x64'
elif arch == 'aarch64': # Another common name for arm64
    arch = 'arm64'

if platform_name == "win32" or arch == "x64":
    bin_dir_name = platform_name
else:
    bin_dir_name = platform_name + arch

# Path to the ReScript language server executable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LSP_SERVER_PATH = os.path.join(SCRIPT_DIR, "node_modules", "@rescript", "language-server", "out", "cli.js")

# --- LSP Communication Helper Functions ---

def start_lsp_server():
    """Starts the language server subprocess."""
    process = subprocess.Popen(
        ['node', '--max-old-space-size=4096', LSP_SERVER_PATH, '--stdio'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr
    )
    return process

def send_notification(process, method, params):
    """Sends an LSP notification."""
    if process.poll() is not None:
        print(f"LSP Client: Server process has terminated. Cannot send notification: {method}")
        return

    request = {
        "jsonrpc": "2.0",
        "method": method,
        "params": converter.unstructure(params)
    }
    encoded_request = json.dumps(request).encode('utf-8')
    header = f"Content-Length: {len(encoded_request)}\r\n\r\n".encode('utf-8')
    try:
        process.stdin.write(header + encoded_request)
        process.stdin.flush()
    except BrokenPipeError:
        print(f"LSP Client: Broken pipe. The LSP server may have crashed.")

def send_response(process, request_id, result):
    """Sends a response to a server-initiated request."""
    if process.poll() is not None:
        print(f"LSP Client: Server process has terminated. Cannot send response for request {request_id}")
        return

    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }
    encoded_response = json.dumps(response).encode('utf-8')
    header = f"Content-Length: {len(encoded_response)}\r\n\r\n".encode('utf-8')
    try:
        process.stdin.write(header + encoded_response)
        process.stdin.flush()
        print(f"LSP Client: Sent response for server request {request_id}")
    except BrokenPipeError:
        print(f"LSP Client: Broken pipe. The LSP server may have crashed.")

def send_request(process, method, params):
    """Sends an LSP request and returns the response."""
    if process.poll() is not None:
        print(f"LSP Client: Server process has terminated. Cannot send request: {method}")
        return None

    global request_id_counter
    request_id = request_id_counter
    request_id_counter += 1

    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": converter.unstructure(params)
    }
    encoded_request = json.dumps(request).encode('utf-8')
    header = f"Content-Length: {len(encoded_request)}\r\n\r\n".encode('utf-8')
    try:
        process.stdin.write(header + encoded_request)
        process.stdin.flush()
    except BrokenPipeError:
        print(f"LSP Client: Broken pipe. The LSP server may have crashed.")
        return None
    return read_response(process, request_id)

def reader_thread(process, message_queue):
    """Reads messages from the LSP server and puts them into a queue."""
    while True:
        header_line = process.stdout.readline()
        if not header_line:
            break
        header = header_line.decode('utf-8').strip()
        if header.startswith("Content-Length"):
            content_length = int(header.split(':')[1].strip())
            process.stdout.readline()
            data = process.stdout.read(content_length).decode('utf-8')
            message_queue.put(data)

def response_processor_thread(message_queue):
    """Processes messages from the queue and dispatches them."""
    while True:
        data = message_queue.get()
        if data is None:
            break
        try:
            message = json.loads(data)
            if 'id' in message and ('result' in message or 'error' in message):
                request_id = message['id']
                lsp_responses[request_id] = message
                if request_id in lsp_response_events:
                    lsp_response_events[request_id].set()
            elif 'method' in message and 'id' in message:
                print(f"LSP Client: Received request from server: {message}")
                send_response(lsp_process, message['id'], None)
            else:
                print(f"LSP Client: Received notification or unexpected message: {message}")
        except json.JSONDecodeError:
            print(f"LSP Client: Failed to decode JSON: {data}")

def read_response(process, expected_id):
    """Waits for a response with a specific ID."""
    if expected_id not in lsp_response_events:
        lsp_response_events[expected_id] = threading.Event()
    
    # Wait for the event to be set, with a timeout
    if lsp_response_events[expected_id].wait(timeout=30):  # 30-second timeout
        response = lsp_responses.pop(expected_id, None)
        lsp_response_events.pop(expected_id, None)
        return response
    else:
        # Timeout occurred
        print(f"LSP Client: Timeout waiting for response for request {expected_id}")
        lsp_response_events.pop(expected_id, None)
        return None

# --- Main Logic ---

def find_potential_references(content):
    """Finds all potential function calls, JSX tags, and module access in the content."""
    if content in file_analysis_cache:
        return file_analysis_cache[content]["refs"]
    # This regex is a bit of a heuristic. It looks for:
    # 1. Uppercase identifiers, which are likely modules or components (e.g., Utils.makeUser, <MyComponent>)
    # 2. `Js.` followed by an identifier, for built-in JS interop (e.g., Js.log)
    # 3. `Array.` for built-in array functions.
    return re.findall(r"([A-Z][\w\.]*|Js\.\w+|Array\.\w+)", content)

def find_jsx_tags(content):
    """Finds all JSX tags (both opening and closing) in the given content."""
    if content in file_analysis_cache and "jsx" in file_analysis_cache[content]:
        return file_analysis_cache[content]["jsx"]
    # This regex finds both opening tags like <Component> and closing tags like </Component>
    # It also handles self-closing tags like <Component />
    # It's not perfect, but it's a good starting point for finding potential components.
    # This regex finds both uppercase and lowercase JSX tags.
    return re.findall(r"<\/?([a-zA-Z][\w\.]*)", content)

def analyze_file_on_demand(lsp_process, file_path, graph, symbol_locations, file_contents):
    """Analyzes a single file, populating the graph and symbol maps."""
    if file_path not in file_locks:
        file_locks[file_path] = threading.Lock()
    with file_locks[file_path]:
        if file_path in file_contents:
            return  # Already analyzed
        
        # Add a placeholder to indicate that the file is being processed
        file_contents[file_path] = "ANALYZING"

    uri = f"file://{file_path}"
    uri = f"file://{file_path}"
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            file_contents[file_path] = content
    except FileNotFoundError:
        print(f"    [WARN] Could not find file: {file_path}")
        return

    open_params = DidOpenTextDocumentParams(
        text_document=TextDocumentItem(uri=uri, language_id="rescript", version=1, text=content)
    )
    send_notification(lsp_process, "textDocument/didOpen", open_params)

    doc_identifier = TextDocumentIdentifier(uri=uri)
    doc_sym_params = DocumentSymbolParams(text_document=doc_identifier)
    symbols_response = send_request(lsp_process, "textDocument/documentSymbol", doc_sym_params)

    if symbols_response and 'result' in symbols_response and symbols_response['result'] is not None:
        with file_locks[file_path]:
            symbols = converter.structure(symbols_response['result'], List[DocumentSymbol])
            process_symbols_recursively(graph, file_path, symbols, symbol_locations, file_contents)

def process_symbols_recursively(graph, file_path, symbols, symbol_locations, file_contents, parent_prefix=""):
    """Recursively processes symbols, adding them to the graph and a location map."""
    lines = file_contents.get(file_path, "").splitlines()
    for symbol in symbols:
        kind = symbol.kind.name.lower()
        if kind not in ["module", "function", "typeparameter", "variable"]:
            if hasattr(symbol, 'children') and symbol.children:
                process_symbols_recursively(graph, file_path, symbol.children, symbol_locations, file_contents, parent_prefix)
            continue
        component_name = f"{parent_prefix}.{symbol.name}" if parent_prefix else symbol.name
        node_name = f"{os.path.basename(file_path)}::{component_name}"
        
        # Extract the code for the symbol
        start_line = symbol.range.start.line
        end_line = symbol.range.end.line
        code_snippet = "\n".join(lines[start_line:end_line+1])

        graph.add_node(node_name, kind=kind, file=file_path, code=code_snippet)
        
        # Map every line within the symbol's range to its full node name
        for line in range(symbol.range.start.line, symbol.range.end.line + 1):
            symbol_locations[f"{file_path}:{line}"] = node_name
        
        if hasattr(symbol, 'children') and symbol.children:
            process_symbols_recursively(graph, file_path, symbol.children, symbol_locations, file_contents, component_name)

def build_repo_graph():
    global lsp_process
    graph = nx.DiGraph()
    rescript_json_files = []
    for root, _, files in os.walk(PROJECT_ROOT):
        if "node_modules" in root:
            continue
        for file in files:
            if file == "rescript.json":
                rescript_json_files.append(root)

    if not rescript_json_files:
        print("No rescript.json files found.")
        return None

    for project_root in rescript_json_files:
        res_files = []
        print(f"Analyzing project at: {project_root}")
        # First, compile the project to generate the necessary analysis files
        print("Compiling ReScript project...")
        try:
            subprocess.run([os.path.join(PROJECT_ROOT, "node_modules", "rescript", "rescript")], check=True, cwd=project_root)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Failed to compile ReScript project: {e}")
            continue

        lsp_process = start_lsp_server()
        if lsp_process is None:
            print("Failed to start ReScript language server.")
            continue

        # Start the reader and processor threads
        threading.Thread(target=reader_thread, args=(lsp_process, message_queue), daemon=True).start()
        threading.Thread(target=response_processor_thread, args=(message_queue,), daemon=True).start()

        # 1. Initialize the LSP Session
        print("Initializing LSP session...")
        init_params = InitializeParams(
            process_id=os.getpid(),
            root_uri=f"file://{project_root}",
            capabilities=ClientCapabilities(workspace=WorkspaceClientCapabilities()),
        )
        send_request(lsp_process, "initialize", init_params)
        send_notification(lsp_process, "initialized", InitializedParams())
        time.sleep(10) # Give server a moment to finish initializing

        # 2. Get list of all .res files
        for root, _, files in os.walk(os.path.join(project_root, "src")):
            for file in files:
                if file.endswith(".res"):
                    res_files.append(os.path.join(root, file))

        print(f"Found {len(res_files)} ReScript files.")

        # 3. Open all documents and build the initial graph
        print("Analyzing project files...")
        symbol_locations: Dict[str, str] = {}
        file_contents: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
            futures = [executor.submit(analyze_file_on_demand, lsp_process, file_path, graph, symbol_locations, file_contents) for file_path in res_files]
            for future in tqdm(concurrent.futures.as_completed(futures), total=len(res_files), desc="Analyzing project files..."):
                future.result()

        # 4. Resolve all references (internal, external, and built-in)
        print("\nResolving all references...")
        graph.add_node("External/Built-in", kind="external", file="", code="")

        with tqdm(total=len(file_contents), desc="Resolving references") as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
                futures = {executor.submit(process_references, lsp_process, graph, symbol_locations, file_contents, source_file_path, source_content.splitlines(), set(find_potential_references(source_content) + find_jsx_tags(source_content))): source_file_path for source_file_path, source_content in file_contents.items()}
                for future in concurrent.futures.as_completed(futures):
                    future.result()
                    pbar.update(1)

        # Stop the server
        print("Shutting down LSP server...")
        lsp_process.kill()
    return graph

def process_references(lsp_process, graph, symbol_locations, file_contents, source_file_path, lines, ref_names):
    """Helper to process different kinds of references (definition, type definition)."""
    for ref_name in ref_names:
        clean_ref_name = ref_name.replace('<', '').replace('/', '')
        for line_num, line in enumerate(lines):
            col = line.find(clean_ref_name)
            if col != -1:
                uri = f"file://{source_file_path}"
                position = Position(line=line_num, character=col)
                
                # Get definition
                def_params = DefinitionParams(text_document=TextDocumentIdentifier(uri=uri), position=position)
                def_response = send_request(lsp_process, "textDocument/definition", def_params)
                
                edge_type = "jsx" if ref_name.startswith('<') else "reference"
                handle_response(lsp_process, graph, symbol_locations, file_contents, source_file_path, line_num, def_response, edge_type)

                # Get type definition
                type_def_params = TypeDefinitionParams(text_document=TextDocumentIdentifier(uri=uri), position=position)
                type_def_response = send_request(lsp_process, "textDocument/typeDefinition", type_def_params)
                handle_response(lsp_process, graph, symbol_locations, file_contents, source_file_path, line_num, type_def_response, "type_definition")

def handle_response(lsp_process, graph, symbol_locations, file_contents, source_file_path, line_num, response, edge_type):
    """Helper to handle LSP responses and add edges to the graph."""
    source_node_name = symbol_locations.get(f"{source_file_path}:{line_num}")
    if not source_node_name:
        return

    if response and 'result' in response and response['result'] is not None:
        results = response['result']
        if not isinstance(results, list):
            results = [results]
        
        for def_loc in results:
            def_file_path = urlparse(def_loc['uri']).path
            def_line = def_loc['range']['start']['line']

            if def_file_path not in file_contents:
                analyze_file_on_demand(lsp_process, def_file_path, graph, symbol_locations, file_contents)

            target_node_name = symbol_locations.get(f"{def_file_path}:{def_line}")
            if target_node_name and source_node_name != target_node_name:
                with graph_lock:
                    graph.add_edge(source_node_name, target_node_name, type=edge_type)
    else:
        # This is likely an external or built-in function
        if edge_type == "reference": # Only add external references for definitions
            with graph_lock:
                graph.add_edge(source_node_name, "External/Built-in", type="external_reference")

# --- Execute and Save ---

if __name__ == "__main__":
    code_graph = build_repo_graph()
    if code_graph:
        print(f"\nGraph created with {code_graph.number_of_nodes()} nodes and {code_graph.number_of_edges()} edges.")
        
        # Save the graph to a file for visualization
        output_gexf = "rescript_repo.gexf"
        nx.write_gexf(code_graph, output_gexf)
        print(f"Graph saved to '{output_gexf}'. Use a tool like Gephi to visualize it.")

        output_graphml = "rescript_repo.graphml"
        nx.write_graphml(code_graph, output_graphml)
        print(f"Graph saved to '{output_graphml}'.")

        output_pkl = "rescript_repo.pkl"
        with open(output_pkl, 'wb') as f:
            pickle.dump(code_graph, f)
        print(f"Graph saved to '{output_pkl}'.")
