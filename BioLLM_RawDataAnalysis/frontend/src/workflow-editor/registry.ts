import type {
  JsonPrimitive,
  NodeDefinition,
  NodeRegistryResponse,
  WorkflowNode,
} from './types'

export type NodeRegistry = Map<string, NodeDefinition>

export function createRegistry(response: NodeRegistryResponse): NodeRegistry {
  return new Map(response.nodes.map(node => [node.type, node]))
}

export function defaultParameters(definition: NodeDefinition): Record<string, JsonPrimitive> {
  return Object.fromEntries(
    Object.entries(definition.parameters_schema)
      .filter(([, schema]) => schema.default !== undefined)
      .map(([key, schema]) => [key, schema.default as JsonPrimitive]),
  )
}

export function createNode(definition: NodeDefinition, position: { x: number; y: number }): WorkflowNode {
  return {
    id: `${definition.type}-${crypto.randomUUID().slice(0, 8)}`,
    type: definition.type,
    parameters: defaultParameters(definition),
    position,
  }
}

export function categoryLabel(category: string): string {
  return ({
    input: '输入',
    qc: '质量控制',
    preprocessing: '预处理',
    annotation: '注释分析',
    mag: 'MAG 分析',
    report: '报告',
  } as Record<string, string>)[category] ?? category
}
