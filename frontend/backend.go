package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type backendEvent struct {
	Type           string    `json:"type"`
	ScenarioID     int       `json:"scenario_id,omitempty"`
	Scenario       string    `json:"scenario,omitempty"`
	GroupID        int       `json:"group_id,omitempty"`
	Sender         string    `json:"sender,omitempty"`
	Role           string    `json:"role,omitempty"`
	Content        string    `json:"content,omitempty"`
	Generation     int       `json:"generation,omitempty"`
	BestScore      float64   `json:"best_score,omitempty"`
	BestPrompt     string    `json:"best_prompt,omitempty"`
	BestGenome     string    `json:"best_genome_id,omitempty"`
	Rubric         string    `json:"rubric,omitempty"`
	AvgScore       float64   `json:"avg_score,omitempty"`
	Diversity      float64   `json:"diversity,omitempty"`
	Overall        float64   `json:"overall,omitempty"`
	GenomeID       string    `json:"genome_id,omitempty"`
	ScenarioScores []float64 `json:"scenario_scores,omitempty"`
	ParentIDs      []string  `json:"parent_ids,omitempty"`
	Survivors      []string  `json:"survivors,omitempty"`
	Eliminated     []string  `json:"eliminated,omitempty"`
	PopSize        int       `json:"population_size,omitempty"`
	NumGens        int       `json:"num_generations,omitempty"`
	NumScenarios   int       `json:"num_scenarios,omitempty"`
	Goal           string    `json:"goal,omitempty"`
}

var FinalResult *EvolutionResult
var CurrentGeneration int
var ActivityLog []string

func logActivity(msg string) {
	ActivityLog = append(ActivityLog, msg)
	if len(ActivityLog) > 50 {
		ActivityLog = ActivityLog[len(ActivityLog)-50:]
	}
}

type GenerationStats struct {
	BestScore  float64
	AvgScore   float64
	Diversity  float64
	Survivors  []string
	Eliminated []string
}

type AgentScore struct {
	GenomeID       string
	Overall        float64
	ScenarioScores []float64
	ParentIDs      []string
	Generation     int
}

type EvolutionResult struct {
	BestPrompt     string
	BestScore      float64
	BestGenome     string
	Goal           string
	Rubric         string
	PopSize        int
	NumGens        int
	Generations    []GenerationStats
	FinalScores    []AgentScore
	AllScores      []AgentScore // all agents across all generations
	ScenarioNames  []string
}

func RunBackend(cells []cell, goal, rubricHint string, generations, population int) {
	go func() {
		defer func() {
			for i := range cells {
				select {
				case <-cells[i].conv.done:
				default:
					close(cells[i].conv.done)
				}
			}
		}()

		logFile, err := os.Create("backend.log")
		if err != nil {
			return
		}
		defer logFile.Close()

		backendDir := filepath.Join("..", "backend")
		pythonPath := filepath.Join(backendDir, "..", ".venv", "bin", "python")

		args := []string{
			"orchestrator.py",
			goal,
			"--population", "6",
			"--generations", fmt.Sprintf("%d", generations),
			"--scenarios", fmt.Sprintf("%d", len(cells)),
		}

		if rubricHint != "" {
			tmpDir, err := os.MkdirTemp("", "agent-kitchen-*")
			if err == nil {
				defer os.RemoveAll(tmpDir)
				rubricPath := filepath.Join(tmpDir, "rubric.txt")
				os.WriteFile(rubricPath, []byte(rubricHint), 0644)
				args = append(args, "--rubric", rubricPath)
			}
		}

		cmd := exec.Command(pythonPath, args...)
		cmd.Dir = backendDir
		cmd.Stderr = logFile
		cmd.Env = append(os.Environ(), "PYTHONDONTWRITEBYTECODE=1")

		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return
		}

		if err := cmd.Start(); err != nil {
			return
		}

		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1024*1024), 1024*1024)

		// Track which group is currently being displayed per cell
		// When a new group_id appears for a scenario, emit a separator
		lastGroup := make(map[int]int) // scenario_id -> last group_id seen

		var genStats []GenerationStats
		var finalScores []AgentScore
		var allScores []AgentScore
		var scenarioNames []string
		var evGoal string
		var popSize, numGens int
		var currentSurvivors, currentEliminated []string
		var currentGen int

		for scanner.Scan() {
			var event backendEvent
			if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
				continue
			}

			switch event.Type {

			case "evolution_start":
				evGoal = event.Goal
				popSize = event.PopSize
				numGens = event.NumGens
				logActivity("Generating scenarios...")

			case "scenario_ready":
				scenarioNames = append(scenarioNames, event.Scenario)
				logActivity(fmt.Sprintf("Scenario: %s", event.Scenario))

			case "population_ready":
				logActivity("Population seeded")
				logActivity("Starting evolution...")

			case "generation_start":
				CurrentGeneration = event.Generation
				currentGen = event.Generation
				finalScores = nil
				logActivity(fmt.Sprintf("── Gen %d ──", event.Generation))
				logActivity("Running simulations...")
				for i := range cells {
					header := fmt.Sprintf("\n── Generation %d ──\n\n", event.Generation)
					select {
					case cells[i].conv.ch <- header:
					default:
					}
				}

			case "scenario_start":
				idx := event.ScenarioID
				if idx < len(cells) {
					cells[idx].scenario = event.Scenario
					for len(scenarioNames) <= idx {
						scenarioNames = append(scenarioNames, "")
					}
					scenarioNames[idx] = event.Scenario
				}

			case "evaluation_start":
				logActivity("Evaluating agents (3x)...")

			case "message":
				idx := event.ScenarioID
				if idx >= len(cells) {
					continue
				}
				// Clean up JSON-wrapped messages
				content := event.Content
				if strings.HasPrefix(content, "{") {
					var wrapper map[string]string
					if err := json.Unmarshal([]byte(content), &wrapper); err == nil {
						if msg, ok := wrapper["message"]; ok {
							content = msg
						}
					}
				}

				// Insert separator when switching to a different agent's conversation
				prev, seen := lastGroup[idx]
				if seen && prev != event.GroupID {
					sep := "~\n" // parsed as separator in styling
					select {
					case cells[idx].conv.ch <- sep:
					default:
					}
				}
				lastGroup[idx] = event.GroupID

				var line string
				if event.Role == "negotiator" {
					line = fmt.Sprintf(">%s\n", content)
				} else {
					line = fmt.Sprintf("<%s: %s\n", event.Sender, content)
				}
				select {
				case cells[idx].conv.ch <- line:
				default:
				}

			case "score":
				as := AgentScore{
					GenomeID:       event.GenomeID,
					Overall:        event.Overall,
					ScenarioScores: event.ScenarioScores,
					ParentIDs:      event.ParentIDs,
					Generation:     currentGen,
				}
				finalScores = append(finalScores, as)
				allScores = append(allScores, as)
				id := event.GenomeID
				if len(id) > 8 {
					id = id[:8]
				}
				logActivity(fmt.Sprintf("  %s → %.0f%%", id, event.Overall*100))

			case "selection":
				currentSurvivors = event.Survivors
				currentEliminated = event.Eliminated
				logActivity(fmt.Sprintf("  %d survived, %d eliminated", len(event.Survivors), len(event.Eliminated)))
				logActivity("Mutating & crossing over...")

			case "generation_complete":
				genStats = append(genStats, GenerationStats{
					BestScore:  event.BestScore,
					AvgScore:   event.AvgScore,
					Diversity:  event.Diversity,
					Survivors:  currentSurvivors,
					Eliminated: currentEliminated,
				})
				currentSurvivors = nil
				currentEliminated = nil
				logActivity(fmt.Sprintf("Gen %d done · best: %.0f%%", event.Generation, event.BestScore*100))
				for i := range cells {
					divider := fmt.Sprintf("\n══ Gen %d done · best: %.0f%% ══\n\n",
						event.Generation, event.BestScore*100)
					select {
					case cells[i].conv.ch <- divider:
					default:
					}
				}

			case "evolution_complete":
				FinalResult = &EvolutionResult{
					BestPrompt:    event.BestPrompt,
					BestScore:     event.BestScore,
					BestGenome:    event.BestGenome,
					Goal:          evGoal,
					Rubric:        event.Rubric,
					PopSize:       popSize,
					NumGens:       numGens,
					Generations:   genStats,
					FinalScores:   finalScores,
					AllScores:     allScores,
					ScenarioNames: scenarioNames,
				}
			}
		}

		cmd.Wait()
	}()
}
