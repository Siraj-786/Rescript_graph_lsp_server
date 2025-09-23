# ReScript Code Graph Generator

This project provides a Python script that analyzes a ReScript repository and generates a dependency graph of its modules, functions, types, and variables. The graph can be exported to various formats, including GEXF, GraphML, and Pickle.

## Features

*   **Monorepo Support**: Automatically discovers and processes all `rescript.json` files in a repository, making it suitable for large monorepos with multiple projects.
*   **Multiple Output Formats**: Exports the graph to GEXF, GraphML, and Pickle formats for further analysis and visualization.
*   **Detailed Symbol Information**: Captures detailed information about each symbol, including its kind, file path, and source code.
*   **Configurable Filtering**: Allows you to easily configure which symbol kinds to include in the graph, enabling you to create both high-level and detailed views of your codebase.

## Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/Siraj-786/Rescript_graph_lsp_server.git
    cd Rescript_graph_lsp_server
    ```

2.  **Install dependencies**:
    Make sure you have Python 3 and Node.js installed. Then, run the following commands to install the required packages:
    ```bash
    pip install -r requirements.txt
    npm install
    ```

## Usage

To generate the graph for a ReScript project, run the `lsp_client.py` script, optionally passing the path to the project's root directory as an argument. If no path is provided, it will analyze the current directory.

```bash
# Analyze the current directory
python3 lsp_client.py

# Analyze a specific project
python3 lsp_client.py /path/to/your/rescript/project
```

The script will automatically find all `rescript.json` files in the specified directory and its subdirectories, and generate the graph for each project.

## Output

The script generates the following files in the same directory where the script is run:

*   `rescript_repo.gexf`: A GEXF file that can be opened with tools like [Gephi](https://gephi.org/) for visualization.
*   `rescript_repo.graphml`: A GraphML file that can be used with various graph analysis libraries.
*   `rescript_repo.pkl`: A Python Pickle file containing the `networkx` graph object, which can be loaded for further analysis in a Python environment.

## How it Works

The script starts a ReScript Language Server Protocol (LSP) server and uses it to analyze the source code. It performs the following steps:

1.  **Compiles the project**: Runs `rescript build` to ensure that all artifacts are up-to-date.
2.  **Initializes the LSP server**: Starts the ReScript LSP server in stdio mode.
3.  **Analyzes files**: Opens all `.res` files in the project and requests the document symbols from the LSP server.
4.  **Builds the graph**: Creates a `networkx` graph and adds nodes for each symbol.
5.  **Resolves references**: For each symbol, it requests the definition and type definition from the LSP server and creates edges in the graph to represent the relationships.
6.  **Exports the graph**: Saves the graph to GEXF, GraphML, and Pickle formats.
