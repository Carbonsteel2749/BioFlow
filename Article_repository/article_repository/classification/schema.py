from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class ArticleTags(BaseModel):
    core_bacteria: str = Field(
        default="",
        description="核心菌种，如双歧杆菌、乳酸杆菌、拟杆菌等，用顿号分隔"
    )
    experiment_model: str = Field(
        default="",
        description="实验模型，如小鼠自闭症模型、大鼠、细胞模型、临床样本等"
    )
    intervention: str = Field(
        default="",
        description="干预手段，如益生菌补充、粪菌移植、抗生素处理、饮食干预等，用顿号分隔"
    )
    analysis_method: str = Field(
        default="",
        description="分析方法，如差异丰度分析、相关性分析、机器学习、通路分析等，用顿号分隔"
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArticleTags":
        return cls(**data)