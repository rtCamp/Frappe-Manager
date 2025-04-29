{
  description = "Nix flake for Frappe Manager";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"; # Or your preferred channel
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        python = pkgs.python311; # Using 3.11 for consistency

        # --- fm-helper Package Definition ---
        fm-helper-pkg = python.pkgs.buildPythonPackage {
          pname = "fm-helper";
          version = "0.2.0";
          src = ./fm-helper;
          format = "pyproject";
          propagatedBuildInputs = [
            python.pkgs.typer # Corresponds to typer[all] (extras often handled)
            python.pkgs.rich
            python.pkgs.supervisor
            # Add shellingham explicitly if typer doesn't pull it in via extras correctly in Nix
            # python.pkgs.shellingham
          ];

          # Build dependencies (usually handled by format="pyproject", but explicit doesn't hurt)
          nativeBuildInputs = [
            python.pkgs.setuptools
            python.pkgs.wheel
            pkgs.makeWrapper # Explicitly add makeWrapper
          ];

          # Optional: Check if the main module can be imported after build
          pythonImportsCheck = [ "fm_helper.cli" ];

          # Important Note on fm-wait-jobs and frappe:
          # The 'frappe' dependency required by the 'fm-wait-jobs' script
          # is *intentionally omitted* here. This Nix package builds only
          # fm-helper and its direct dependencies. The 'frappe' framework
          # must be present in the runtime environment (e.g., the Docker container)
          # where 'fm-wait-jobs' is executed.

          meta = with pkgs.lib; {
            description = "CLI tool to interact with supervisord instances managed by Frappe Manager (for use inside containers)";
            homepage = "https://github.com/rtCamp/Frappe-Manager"; # Adjust if fm-helper gets its own page
            license = licenses.mit; # Assuming MIT, confirm license
            maintainers = with maintainers; [ ]; # Add your handle
            platforms = platforms.linux; # Primarily for Linux containers
          };
        };

      in
      {
        packages = {
          # Package accessible via `nix build .#fm-helper`
          fm-helper = fm-helper-pkg;

          # You could add the main 'frappe-manager' package build here too
          # frappe-manager = ...;
        };

        # Default package when running `nix build .#fm-helper`
        defaultPackage = self.packages.${system}.fm-helper;

        # Development shell for working on fm-helper
        devShells.fm-helper = pkgs.mkShell {
           name = "fm-helper-dev";
           # Include build inputs
           inputsFrom = [ fm-helper-pkg ];
           # Add the package itself to the shell for testing
           packages = [ fm-helper-pkg ];

           shellHook = ''
             echo "Entering fm-helper dev shell..."
             # You can add other dev setup here if needed
           '';
        };

        # You could add a devShell for the main 'frappe-manager' here too
        # devShells.default = ...;

      });
}
