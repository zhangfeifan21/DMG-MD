# NewMD v0.1

NewMD 是一个最小的单 NVIDIA GPU CUDA 项目骨架。当前可执行程序仅查询
CUDA 设备 0，并打印设备名称；尚未实现 Atom、NeighborList、Potential 或
Integrator。

## 环境要求

- 支持 C++17 的编译器
- CUDA Toolkit（包含 `nvcc`）
- CMake 3.18 或更高版本
- 一块可用的 NVIDIA GPU

## 构建与测试

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build -j
ctest --test-dir build --output-on-failure
```

## 运行

```bash
./build/newmd
```

程序成功运行时会输出类似：

```text
CUDA device 0: NVIDIA GPU Name
```
