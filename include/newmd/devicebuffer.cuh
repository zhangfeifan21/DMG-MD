#ifndef DEVICE_BUFFER_CUH_
#define DEVICE_BUFFER_CUH_

#include <cuda_runtime.h>
#include <cstddef>
#include <stdexcept>
#include <utility>

template <typename T>
class DeviceBuffer {
public:
    DeviceBuffer() = default;

    explicit DeviceBuffer(std::size_t size) {
        resize(size);
    }

    ~DeviceBuffer() {
        clear();
    }

    // 禁用拷贝
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;

    // 移动构造函数
    DeviceBuffer(DeviceBuffer&& other) noexcept {
        ptr_ = other.ptr_;
        size_ = other.size_;
        other.ptr_ = nullptr;
        other.size_ = 0;
    }

    // 移动赋值运算符
    DeviceBuffer& operator=(DeviceBuffer&& other) noexcept {
        if (this != &other) {
            clear();
            ptr_ = other.ptr_;
            size_ = other.size_;
            other.ptr_ = nullptr;
            other.size_ = 0;
        }
        return *this;
    }

    void resize(std::size_t size) {
        if (size == size_) return;

        clear();
        if (size > 0) {
            cudaError_t err = cudaMalloc(&ptr_, size * sizeof(T));
            if (err != cudaSuccess) {
                throw std::runtime_error(cudaGetErrorString(err));
            }
            size_ = size;
        }
    }

    void clear() {
        if (ptr_ != nullptr) {
            cudaFree(ptr_);
            ptr_ = nullptr;
        }
        size_ = 0;
    }

    T* data() { return ptr_; }
    const T* data() const { return ptr_; }

    std::size_t size() const { return size_; }

    void copy_from_host(const T* src, std::size_t count) {
        if (count > size_) {
            throw std::out_of_range("Copy count exceeds DeviceBuffer size.");
        }
        cudaError_t err = cudaMemcpy(ptr_, src, count * sizeof(T), cudaMemcpyHostToDevice);
        if (err != cudaSuccess) {
            throw std::runtime_error(cudaGetErrorString(err));
        }
    }

    void copy_to_host(T* dst, std::size_t count) const {
        if (count > size_) {
            throw std::out_of_range("Copy count exceeds DeviceBuffer size.");
        }
        cudaError_t err = cudaMemcpy(dst, ptr_, count * sizeof(T), cudaMemcpyDeviceToHost);
        if (err != cudaSuccess) {
            throw std::runtime_error(cudaGetErrorString(err));
        }
    }

private:
    T* ptr_ = nullptr;
    std::size_t size_ = 0;
};

#endif // DEVICE_BUFFER_CUH_