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
      cubism-core = pkgs.stdenvNoCC.mkDerivation {
        pname = "cubism-core";
        version = "5-r.5";
        src = pkgs.fetchurl {
          url = "https://cubism.live2d.com/sdk-native/bin/CubismSdkForNative-5-r.5.zip";
          hash = "sha256-f/Oku8GcCocollqlIqt36xGyUpFkU+aKinjTtxGIuxI=";
        };
        nativeBuildInputs = [ pkgs.unzip pkgs.autoPatchelfHook ];
        dontBuild = true;
        installPhase = ''
          runHook preInstall
          install -Dm644 Core/dll/linux/x86_64/libLive2DCubismCore.so "$out/lib/libLive2DCubismCore.so"
          install -Dm644 Core/include/Live2DCubismCore.h "$out/include/Live2DCubismCore.h"
          install -Dm644 Core/LICENSE.md "$out/share/licenses/cubism-core/LICENSE.md"
          runHook postInstall
        '';
        meta = {
          license = pkgs.lib.licenses.unfree;
          sourceProvenance = [ pkgs.lib.sourceTypes.binaryNativeCode ];
          platforms = [ "x86_64-linux" ];
        };
      };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.python312 pkgs.uv pkgs.jdk21 pkgs.git pkgs.ffmpeg pkgs.llama-cpp pkgs.docker-compose cubism-core ];
        CUBISM_CORE_LIBRARY = "${cubism-core}/lib/libLive2DCubismCore.so";
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
