<div align="center">

<img src="https://raw.githubusercontent.com/open-edge-platform/anomalib/main/docs/source/_static/images/logos/anomalib-wide-blue.png" width="600px" alt="Anomalib Logo - A deep learning library for anomaly detection">

**A library for benchmarking, developing and deploying deep learning anomaly detection algorithms**

---

[Key Features](#key-features) •
[Docs](https://anomalib.readthedocs.io/en/latest/) •
[Notebooks](examples/notebooks) •
[License](LICENSE)

![python](https://img.shields.io/badge/python-3.10%2B-green)
![pytorch](https://img.shields.io/badge/pytorch-2.10%2B-orange)
![lightning](https://img.shields.io/badge/lightning-2.2%2B-blue)
![openvino](https://img.shields.io/badge/openvino-2024.0%2B-purple)

[![Pre-Merge Checks](https://github.com/open-edge-platform/anomalib/actions/workflows/pre_merge.yml/badge.svg)](https://github.com/open-edge-platform/anomalib/actions/workflows/pre_merge.yml)
[![codecov](https://codecov.io/gh/open-edge-platform/anomalib/branch/main/graph/badge.svg?token=Z6A07N1BZK)](https://codecov.io/gh/open-edge-platform/anomalib)
[![Downloads](https://static.pepy.tech/personalized-badge/anomalib?period=total&units=international_system&left_color=grey&right_color=green&left_text=PyPI%20Downloads)](https://pepy.tech/project/anomalib)
[![snyk](https://snyk.io/advisor/python/anomalib/badge.svg)](https://snyk.io/advisor/python/anomalib)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/8330/badge)](https://www.bestpractices.dev/projects/8330)

[![ReadTheDocs](https://readthedocs.org/projects/anomalib/badge/?version=latest)](https://anomalib.readthedocs.io/en/latest/?badge=latest)
[![Anomalib - Gurubase docs](https://img.shields.io/badge/Gurubase-Ask%20Anomalib%20Guru-006BFF)](https://gurubase.io/g/anomalib)

<a href="https://trendshift.io/repositories/14514" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/repositories/14514" alt="open-edge-platform%2Fanomalib | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

</div>

---

> 🌟 **Announcing v2.7.0 Release!** 🌟
>
> This minor release adds new models and hardens checkpoint and path handling, while removing APIs scheduled for 2.7.0.
>
> Key Changes
>
> - **New models**: MH-PatchCore, RAD (retrieval-based), and FoundAD (few-shot).
> - **Security / API**: Checkpoint loading defaults to `weights_only=True`. When a dataset `root` is set, metadata paths must resolve under that root.
> - **Removals**: Legacy ONNX exporter (`dynamo=False`) is gone — use `dynamo=True` (default) with `anomalib[openvino]`. Pre-timm ViT checkpoint migration is no longer supported. Minimum PyTorch is 2.10+ (ROCm 2.13+).
>
> We value your input! Please share feedback via [GitHub Issues](https://github.com/open-edge-platform/anomalib/issues) or our [Discussions](https://github.com/open-edge-platform/anomalib/discussions)

# 👋 Introduction

Anomalib is a deep learning library that aims to collect state-of-the-art anomaly detection algorithms for benchmarking on both public and private datasets. Anomalib provides several ready-to-use implementations of anomaly detection algorithms described in the recent literature, as well as a set of tools that facilitate the development and implementation of custom models. The library has a strong focus on visual anomaly detection, where the goal of the algorithm is to detect and/or localize anomalies within images or videos in a dataset. Anomalib is constantly updated with new algorithms and training/inference extensions, so keep checking!

<p align="center">
  <img src="https://raw.githubusercontent.com/open-edge-platform/anomalib/main/docs/source/_static/images/readme.png" width="1000" alt="A prediction made by anomalib">
</p>

## Key features

- Simple and modular API and CLI for training, inference, and benchmarking.
- The largest public collection of ready-to-use deep learning anomaly detection algorithms and benchmark datasets.
- [**Lightning**](https://www.lightning.ai/) based model implementations to reduce boilerplate code and limit the implementation efforts to the bare essentials.
- The majority of models can be exported to [**OpenVINO**](https://www.intel.com/content/www/us/en/developer/tools/openvino-toolkit/overview.html) Intermediate Representation (IR) for accelerated inference on Intel hardware.
- A set of [inference tools](tools) for quick and easy deployment of the standard or custom anomaly detection models.

## RDT Reliability Monitor: local workstation reference

The standalone Windows camera/inspection UI is in [application/rdt_monitor](application/rdt_monitor).
See its [operator and development guide](application/rdt_monitor/README.md) for the full workflow.
See the [implemented code pipeline diagram](TechRequirement/Working%20Pipeline.svg) for linked paths through the current camera, synchronization, training, inference, and evidence code.
The paths below describe this workstation, whose repository is **`D:\anomalib`**.
Generated IDs and operator-selected directories vary between collections and runs.
This is an **unqualified development inspection system**, not a production defect-detection guarantee.

### What is included in Git

This repository uses a **source/configuration-only** policy for the RDT workstation.
Commit the desktop source and styles, `pyproject.toml`, `uv.lock`, `hatch_build.py`,
tests and test fixtures, documentation, and `tools/keyence_nr_cm01_api.py`.
The camera helper is required to build/install the desktop package.

The root `.gitignore` deliberately excludes local `models`, `recipes`,
`inspection-results`, and the old root `coil-01-recipe.json`. Training `datasets`,
Python environments, caches and build outputs are also excluded. The paths and
recipe names documented below describe local files; **cloning the repository does
not download these assets**. No Git LFS configuration is needed for these excluded models.

On another workstation, install dependencies using the commands below and download the
backbone to its documented location if training offline. Either collect/review/train a
new specimen and save its inspection recipe, or separately copy a trusted recipe JSON
**together with its matching detector directory** into `recipes`. Back up local recipes,
model bundles and evidence separately; pushing Git does not back them up.

### Start the UI

Run from PowerShell:

```powershell
Set-Location D:\anomalib
uv sync --project application\rdt_monitor --group dev --extra inference
uv run --project application\rdt_monitor rdt-monitor
```

The first command after changing directory prepares the UI environment, including OpenVINO.
For later launches, only the `uv run` command is needed.

| Component | Local path / name |
| --- | --- |
| Desktop project | `D:\anomalib\application\rdt_monitor` |
| UI entry point | `D:\anomalib\application\rdt_monitor\src\rdt_monitor\app.py` (`rdt-monitor`) |
| Desktop Python environment | `D:\anomalib\application\rdt_monitor\.venv\Scripts\python.exe` |
| Training Python environment | `D:\anomalib\.venv\Scripts\python.exe` |
| Training implementation | `D:\anomalib\application\rdt_monitor\src\rdt_monitor\training.py` |
| Camera integration source | `D:\anomalib\tools\keyence_nr_cm01_api.py` |

The two Python environments serve different purposes: the lightweight desktop runs the UI
and OpenVINO inference; the training interpreter needs Anomalib, Torch and export dependencies.
The collection dialog's **Training Python** field selects the latter. Generic camera index `0`
was used for the local USB camera; an OpenCV index does not verify camera identity.

### Model and pretrained backbone

| Setting | Value |
| --- | --- |
| Anomaly-detection algorithm | **PatchCore** (Python class `anomalib.models.Patchcore`) |
| Pretrained feature-extractor backbone | **Wide ResNet-50-2**, timm name `wide_resnet50_2.racm_in1k` |
| Official weights repository | [timm/wide_resnet50_2.racm_in1k](https://huggingface.co/timm/wide_resnet50_2.racm_in1k) |
| Direct backbone download | [model.safetensors](https://huggingface.co/timm/wide_resnet50_2.racm_in1k/resolve/main/model.safetensors) |
| Local backbone file | `D:\anomalib\models\backbones\wide_resnet50_2.racm_in1k\model.safetensors` |
| Trained inference format | OpenVINO IR: matching `model.xml` **and** `model.bin` |
| Training / inference device | CPU |
| Exported model input | One RGB image, NCHW `1 x 3 x 256 x 256`; ONNX opset 18 export |

The backbone is a reusable pretrained feature extractor, **not a generative model or a
universal ready-to-use defect detector**. PatchCore is fitted on reviewed normal ROI crops
for the specimen, camera and pose. Training prefers the local backbone file above and
does not download weights when it is present and valid. Missing local weights permit
the normal HTTPS/cache loading; invalid local weights fail explicitly. Restart training
after placing a downloaded file. Live inference uses the recipe's trained XML/BIN bundle,
not this backbone file directly.

### Recipes: load from one folder

All recipe Load/Save dialogs start in **[recipes](recipes)** (`D:\anomalib\recipes`).
Load a JSON file with **Load recipe**, connect the camera, then **Start** and confirm
the synchronization setup. Loading alone does not start a run.

| Existing recipe | Model bundle beside the JSON | Purpose |
| --- | --- | --- |
| `D:\anomalib\recipes\coil-01-inspection.json` | `detector-ec81403161564bd8b3db18c7734f3c3f\model.xml` and `model.bin` | Model-bound `coil-01` inspection |
| `D:\anomalib\recipes\inspection-recipe.json` | `detector-8862bfe3965d4c70907c7fc155b3d849\model.xml` and `model.bin` | Separate saved `coil-01` inspection binding; different ROI/BIN identity |
| `D:\anomalib\recipes\coil-01-counting.json` | None | Synchronization/cycle counting only; no defect detector |

Keep each recipe JSON and its sibling `detector-<id>` directory together when copying or
moving them. Do not mix XML/BIN files from different bundles. Recipes record specimen ID,
source dimensions, marker/timing settings and, for inspection, model paths/SHA-256 hashes,
inspection ROI and threshold. New schema-4 recipes also store the review margin.
The existing schema-3 inspection recipes above use a backward-compatible margin of `0.05`.
Both currently specify `640 x 480` source images and threshold `0.50`; verify pose and ROI
against the actual specimen rather than choosing a recipe by filename alone.

Default verdicts are green below `0.45`, yellow from `0.45` through `0.55` inclusive, and
red above `0.55`. Grey means no valid completed inspection/evidence. Scores are normalized
anomaly scores, not defect probabilities. Only red model verdicts latch the separate alarm.
Training completion does not activate a model: explicitly **Save inspection recipe**, then load it.

### Collections and training outputs

The default collection parent is `D:\anomalib\datasets\rdt-candidates` when launched from
the repository root. The output folder is editable. Each collection creates a
`collection-<id>` folder containing `collection.json`, `source-0001.png`,
`crop-0001.png`, and subsequent numbered source/ROI pairs. Review all 20-30 candidates
and explicitly confirm the specimen remained undamaged before choosing **Train PatchCore**.

Existing local collection:

```text
D:\anomalib\datasets\rdt-candidates\Coil\collection-dca5197549e54d4cbe5a07463c9658ff
```

Existing successful training job under that collection:

```text
training-e69ca3ade5e542fd87499b4c6bdc4c2b\
  dataset.json                 Confirmed collection snapshot and identity
  good\                        Exact normal ROI crops used for fitting
  fit\                         Anomalib/Lightning fitting and checkpoint outputs
  weights\openvino\model.xml    Exported detector structure
  weights\openvino\model.bin    Matching detector weights
```

Its original saved recipe is in the same `weights\openvino` directory as
`inspection-recipe.json`; use the centralized recipe copies above for everyday loading.
Training also writes `result-<id>.json` in the collection directory.
To reuse an export after restarting, choose **Inspection > Collect and train > Load collection**,
then **Load trained model** in Training and select that job's `weights\openvino\model.xml`.

### Per-cycle results and visible ROI

**Open inspection results** opens **[inspection-results](inspection-results)**
(`D:\anomalib\inspection-results`). New runs use:

```text
D:\anomalib\inspection-results\
  run-<id>\
    green-normal\cycle-00000001\
    yellow-review\cycle-00000002\
    red-defect\cycle-00000003\
    grey-uninspected\cycle-00000004\
```

Each successful cycle directory contains `source.png` (exact inspected frame),
`roi.png` (inspection crop), `map.png` (normalized anomaly map), and `result.json`
(run/cycle/frame/time, recipe/model identity, hashes, score, threshold/margin, verdict
and errors). Failed cycles may have only available images or metadata; check the
record's missing-image/error fields. Counting-only runs do not generate inspection evidence.
The development budget is 512 MiB per run with no automatic deletion; unavailable storage
is reported explicitly. Older `recipes\live-runs` output is left untouched.

The live camera outlines **Inspection ROI** in cyan. The bottom-right panel shows the
latest exact-cycle ROI with a red anomaly heatmap; stronger red marks model-suspected
anomalies, not confirmed physical damage. It updates after each cycle result, stays visible
between cycles and after Stop, and resets on a new run or recipe. The **Event log** is always
visible at the bottom; **Details** separately reveals technical status.

# 📦 Installation

Anomalib can be installed from PyPI. We recommend using a virtual environment and a modern package installer like `uv` or `pip`.

## 🚀 Quick Install

For a standard installation, you can use `uv` or `pip`. This will install the latest version of Anomalib with its core dependencies. PyTorch will be installed based on its default behavior, which usually works for CPU and standard CUDA setups.

```bash
# With uv
uv pip install anomalib

# Or with pip
pip install anomalib
```

For more control over the installation, such as specifying the PyTorch backend (e.g., XPU, CUDA and ROCm) or installing extra dependencies for specific models, see the advanced options below.

<details>
<summary><strong>💡 Advanced Installation: Specify Hardware Backend</strong></summary>

To ensure compatibility with your hardware, you can specify a backend during installation. This is the recommended approach for production environments and for hardware other than CPU or standard CUDA.

**Using `uv`:**

```bash
# CPU support (default, works on all platforms)
uv pip install "anomalib[cpu]"

# CUDA 12.6 support (Linux/Windows with NVIDIA GPU)
uv pip install "anomalib[cu126]"

# CUDA 13.0 support (Linux/Windows with NVIDIA GPU)
uv pip install "anomalib[cu130]"

# ROCm support (Linux with AMD GPU)
uv pip install "anomalib[rocm]"

# Intel XPU support (Linux with Intel GPU)
uv pip install "anomalib[xpu]"
```

**Using `pip`:**
The same extras can be used with `pip`:

```bash
pip install "anomalib[cu130]"
```

</details>

<details>
<summary><strong>🧩 Advanced Installation: Additional Dependencies</strong></summary>

Anomalib includes most dependencies by default. For specialized features, you may need additional optional dependencies. Remember to include your hardware-specific extra.

```bash
# Example: Install with OpenVINO support and CUDA 13.0
uv pip install "anomalib[openvino,cu130]"

# Example: Install all optional dependencies for a CPU-only setup
uv pip install "anomalib[full,cpu]"
```

Here is a list of available optional dependency groups:

| Extra         | Description                              | Purpose                                     |
| :------------ | :--------------------------------------- | :------------------------------------------ |
| `[openvino]`  | Intel OpenVINO optimization              | For accelerated inference on Intel hardware |
| `[clip]`      | Vision-language models                   | `winclip`                                   |
| `[vlm]`       | Vision-language model backends           | Advanced VLM features                       |
| `[loggers]`   | Experiment tracking (wandb, comet, etc.) | For experiment management                   |
| `[notebooks]` | Jupyter notebook support                 | For running example notebooks               |
| `[full]`      | All optional dependencies                | All optional features                       |

</details>

<details>
<summary><strong>🔧 Advanced Installation: Install from Source</strong></summary>

For contributing to `anomalib` or using a development version, you can install from source.

**Using `uv`:**
This is the recommended method for developers as it uses the project's lock file for reproducible environments.

```bash
git clone https://github.com/open-edge-platform/anomalib.git
cd anomalib

# Create the virtual environment
uv venv

# Sync with the lockfile for a specific backend (e.g., CPU)
uv sync --extra cpu

# Or for a different backend like CUDA 13.0
uv sync --extra cu130

# To set up a full development environment
uv sync --extra dev --extra cpu
```

**Using `pip`:**

```bash
git clone https://github.com/open-edge-platform/anomalib.git
cd anomalib

# Install in editable mode with a specific backend
pip install -e ".[cpu]"

# Install with development dependencies
pip install -e ".[dev,cpu]"
```

</details>

# 🧠 Training

Anomalib supports both API and CLI-based training approaches:

## 🔌 Python API

```python
from anomalib.data import MVTecAD
from anomalib.models import Patchcore
from anomalib.engine import Engine

# Initialize components
datamodule = MVTecAD()
model = Patchcore()
engine = Engine()

# Train the model
engine.fit(datamodule=datamodule, model=model)
```

## ⌨️ Command Line

```bash
# Train with default settings
anomalib train --model Patchcore --data anomalib.data.MVTecAD

# Train with custom category
anomalib train --model Patchcore --data anomalib.data.MVTecAD --data.category transistor

# Train with config file
anomalib train --config path/to/config.yaml
```

# 🤖 Inference

Anomalib provides multiple inference options including Torch, Lightning, Gradio, and OpenVINO. Here's how to get started:

## 🔌 Python API

```python
# Load model and make predictions
predictions = engine.predict(
    datamodule=datamodule,
    model=model,
    ckpt_path="path/to/checkpoint.ckpt",
)
```

## ⌨️ Command Line

```bash
# Basic prediction
anomalib predict --model anomalib.models.Patchcore \
                 --data anomalib.data.MVTecAD \
                 --ckpt_path path/to/model.ckpt

# Prediction with results
anomalib predict --model anomalib.models.Patchcore \
                 --data anomalib.data.MVTecAD \
                 --ckpt_path path/to/model.ckpt \
                 --return_predictions
```

> 📘 **Note:** For advanced inference options including Gradio and OpenVINO, check our [Inference Documentation](https://anomalib.readthedocs.io).

# Training on Intel GPUs

> [!Note]
> Currently, only single GPU training is supported on Intel GPUs.
> These commands were tested on Arc 750 and Arc 770.

Ensure that you have PyTorch with XPU support installed. For more information, please refer to the [PyTorch XPU documentation](https://pytorch.org/docs/stable/notes/get_start_xpu.html)

## 🔌 API

```python
from anomalib.data import MVTecAD
from anomalib.engine import Engine, SingleXPUStrategy, XPUAccelerator
from anomalib.models import Stfpm

engine = Engine(
    strategy=SingleXPUStrategy(),
    accelerator=XPUAccelerator(),
)
engine.train(Stfpm(), datamodule=MVTecAD())
```

## ⌨️ CLI

```bash
anomalib train --model Padim --data MVTecAD --trainer.accelerator xpu --trainer.strategy xpu_single
```

# 🧪 Experiment Management

Track your experiments with popular logging platforms through [PyTorch Lightning loggers](https://lightning.ai/docs/pytorch/stable/extensions/logging.html):

- 📊 Weights & Biases
- 📈 Comet.ml
- 📉 TensorBoard

Enable logging in your config file to track:

- Hyperparameters
- Metrics
- Model graphs
- Test predictions

> 📘 **Note:** For logging setup, see our [Logging Documentation](https://anomalib.readthedocs.io/en/latest/markdown/guides/reference/loggers/index.html).

# 📊 Benchmarking

Evaluate and compare model performance across different datasets:

```bash
# Run benchmarking with default configuration
anomalib benchmark --config tools/experimental/benchmarking/sample.yaml
```

> 💡 **Tip:** Check individual model performance in their respective README files:
>
> - [Patchcore Results](src/anomalib/models/image/patchcore/README.md#mvtec-ad-dataset)
> - [Other Models](src/anomalib/models/)

# Anomalib Studio

> [!IMPORTANT]
> Anomalib Studio is currently under active development and should be considered a pre-release. Features may change, and some functionality may be incomplete or unstable. We welcome feedback and contributions as we work towards a stable release.

Anomalib Studio is a low/no-code web application that allows users to train and deploy anomaly detection models. It enables users to leverage Anomalib's features in their operational environment. Users can connect USB and IP cameras, or use a folder of images, as input to the training pipeline. The tool allows direct output to their industrial pipelines through ROS messages, MQTT, etc.

<p align="center">
  <img src="docs/source/_static/images/anomalib_studio.png" alt="Anomalib Studio" />
</p>

The source code for Anomalib Studio lives in the [application](application) folder.

Anomalib Studio is available as two distributions:

1. As a [docker container](application/docker)
2. As a [standalone application](application/ui)

For more information on each, refer to the respective README files.

## Get started with development build

### Setup Backend Dependencies

> [!NOTE]
> This assumes that you have `uv` installed. If not, please refer to the [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

```bash
cd application/backend
uv sync --extra xpu # or uv sync --extra cu130 for CUDA 13.0, uv sync --extra cpu for CPU
```

### Setup Frontend Dependencies

```bash
cd application/ui
npm install
```

### Run the application

> [!IMPORTANT]
> Both backend and frontend dependencies should be installed before running the application.

Run the backend server:

```bash
cd application/backend
./run.sh
```

Run the frontend server:

```bash
cd application/ui
npm run start
```

Navigate to [http://localhost:3000](http://localhost:3000) to see the application.

# ✍️ Reference

If you find Anomalib useful in your research or work, please cite:

```tex
@inproceedings{akcay2022anomalib,
  title={Anomalib: A deep learning library for anomaly detection},
  author={Akcay, Samet and Ameln, Dick and Vaidya, Ashwin and Lakshmanan, Barath and Ahuja, Nilesh and Genc, Utku},
  booktitle={2022 IEEE International Conference on Image Processing (ICIP)},
  pages={1706--1710},
  year={2022},
  organization={IEEE}
}
```

# 👥 Contributing

We welcome contributions! Check out our [Contributing Guide](CONTRIBUTING.md) to get started.

<p align="center">
  <a href="https://github.com/open-edge-platform/anomalib/graphs/contributors">
    <img src="https://contrib.rocks/image?repo=open-edge-platform/anomalib" alt="Contributors to open-edge-platform/anomalib" />
  </a>
</p>

<p align="center">
  <b>Thank you to all our contributors!</b>
</p>
