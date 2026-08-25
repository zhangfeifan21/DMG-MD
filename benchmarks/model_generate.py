from ase.build import bulk
from ase.io import write

import os

os.makedirs('correctness/diamond', exist_ok=True)

# 1. 创建 Diamond C 胞 (也可以选 'cu', 'fcc' 或 'w', 'bcc')
atoms = bulk('C', 'diamond', a=3.567, cubic=True)

# 2. 扩胞 (例如 16x16x16 复制，包含 32,768 个原子)
atoms = atoms * (16, 16, 16)

print("Number of atoms:", len(atoms))
print("Cell:")
print(atoms.cell)
print("PBC:", atoms.pbc)

# 3. 导出为 GPUMD 要求的 Extended XYZ 格式
write('correctness/diamond/model.xyz', atoms, format='extxyz')
