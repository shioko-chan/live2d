{
  description = "Local AIGC to Live2D development environment";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config = { allowUnfree = true; cudaSupport = true; cudaCapabilities = [ "7.5" ]; };
      };
      cuda = pkgs.cudaPackages;
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.python312 pkgs.uv pkgs.jdk21 pkgs.git pkgs.ffmpeg pkgs.llama-cpp pkgs.docker-compose ];
        UV_PYTHON = "${pkgs.python312}/bin/python";
        JAVA_HOME = "${pkgs.jdk21}";
        TORCH_CUDA_ARCH_LIST = "7.5";
        TRITON_LIBCUDA_PATH = "/run/opengl-driver/lib";
        LD_LIBRARY_PATH = "/run/opengl-driver/lib:${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib pkgs.libglvnd cuda.cuda_cudart cuda.libcublas cuda.libcusparse cuda.libnvjitlink ]}";
        shellHook = ''
          if [ -d "$PWD/.venv/bin" ]; then export PATH="$PWD/.venv/bin:$PATH"; fi
          echo "Live2D shell ready. No model services started."
        '';
      };
    };
}
