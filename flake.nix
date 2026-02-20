{
  description = "Queued - TUI SFTP download manager";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python314;
      in
      {
        packages = {
          default = python.pkgs.buildPythonApplication {
            pname = "queued";
            version = "0.1.0";
            pyproject = true;
            src = ./.;

            nativeBuildInputs = with python.pkgs; [
              hatchling
            ];

            propagatedBuildInputs = with python.pkgs; [
              textual
              asyncssh
              aiofiles
              click
            ];

            # Skip tests for now
            doCheck = false;

            meta = with pkgs.lib; {
              description = "TUI SFTP download manager";
              homepage = "https://github.com/jack/queued";
              license = licenses.mit;
              maintainers = [ ];
              mainProgram = "queued";
            };
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
          ];

          shellHook = ''
            echo "Queued development shell"
            echo "Run 'uv sync' to install dependencies"
            echo "Run 'uv run queued' to start the app"
          '';
        };

        # For 'nix run'
        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/queued";
        };
      }
    );
}
