// The same setup request is used by the status-bar command and the chat shortcut.
export const SIMULATION_SETUP_REQUEST =
  "Help me set up a simulation for this project. First inspect the actual project with your available " +
  "read-only tools: its README and build instructions, source entry points, dependency/build files, " +
  "and example inputs. Identify its language and existing workflow from those files. For Python, " +
  "explain the environment, dependencies, and script or module to run. For Fortran or other compiled " +
  "code, identify the compiler and build system, module dependencies and linking requirements; " +
  "separate compilation/build commands from the command that runs the executable. Include input " +
  "files, runtime arguments, working directory, and how to capture output for monitoring. Use " +
  "the project's existing tools and instructions; do not invent file names, installed dependencies, " +
  "or compiler flags. Ask a focused question if something essential is missing. Give me clear, " +
  "copyable terminal commands to review and run myself. Do not execute commands, install packages, " +
  "start a simulation, or edit files.";
