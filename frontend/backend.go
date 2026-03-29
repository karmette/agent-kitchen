package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

type backendEvent struct {
	Type        string  `json:"type"`
	ScenarioID  int     `json:"scenario_id,omitempty"`
	Scenario    string  `json:"scenario,omitempty"`
	GroupID     int     `json:"group_id,omitempty"`
	Sender      string  `json:"sender,omitempty"`
	Role        string  `json:"role,omitempty"`
	Content     string  `json:"content,omitempty"`
	Generation  int     `json:"generation,omitempty"`
	BestScore   float64 `json:"best_score,omitempty"`
	BestPrompt  string  `json:"best_prompt,omitempty"`
	BestGenome  string  `json:"best_genome_id,omitempty"`
}

// ResultChannel is used to send the final result back to the TUI
var FinalResult *EvolutionResult

type EvolutionResult struct {
	BestPrompt string
	BestScore  float64
	BestGenome string
}

func RunBackend(cells []cell, goal string, generations, population int) {
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

		cmd := exec.Command(
			pythonPath, "orchestrator.py",
			goal,
			"--population", fmt.Sprintf("%d", population),
			"--generations", fmt.Sprintf("%d", generations),
		)
		cmd.Dir = backendDir
		cmd.Stderr = logFile

		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return
		}

		if err := cmd.Start(); err != nil {
			return
		}

		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1024*1024), 1024*1024)

		for scanner.Scan() {
			var event backendEvent
			if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
				continue
			}

			switch event.Type {

			case "generation_start":
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
					// Store scenario name on the cell for the header bar
					cells[idx].scenario = event.Scenario
				}

			case "message":
				idx := event.ScenarioID
				if idx >= len(cells) {
					continue
				}

				var line string
				if event.Role == "negotiator" {
					line = fmt.Sprintf("▶ [AGENT] %s:\n  %s\n\n", event.Sender, event.Content)
				} else {
					line = fmt.Sprintf("◀ [%s]:\n  %s\n\n", event.Sender, event.Content)
				}
				select {
				case cells[idx].conv.ch <- line:
				default:
				}

			case "simulation_complete":
				// No separator needed — generation_complete handles it

			case "generation_complete":
				for i := range cells {
					divider := fmt.Sprintf("\n══ Gen %d done · best: %.2f ══\n\n",
						event.Generation, event.BestScore)
					select {
					case cells[i].conv.ch <- divider:
					default:
					}
				}

			case "evolution_complete":
				FinalResult = &EvolutionResult{
					BestPrompt: event.BestPrompt,
					BestScore:  event.BestScore,
					BestGenome: event.BestGenome,
				}
			}
		}

		cmd.Wait()
	}()
}
